"""
Slack Canvas API 래퍼.

전략:
  1) 최초 1회: canvases.create로 Canvas를 만들고 ID를 받아 Secret에 저장
  2) 이후 매일: 동일 Canvas의 모든 섹션을 lookup → replace로 갈아끼움

이 방식의 장점:
  - Canvas의 "공유 상태"와 "URL"이 유지됨 (사람들이 북마크해둘 수 있음)
  - 알림이 과하게 가지 않음
  - 히스토리는 우리가 따로 관리하면 됨
"""

from __future__ import annotations

import os
from typing import Any

import requests

SLACK_API = "https://slack.com/api"


class SlackCanvasClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ["SLACK_BOT_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            }
        )

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(f"{SLACK_API}/{method}", json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error ({method}): {data}")
        return data

    # ------- 최초 1회 -------
    def create_canvas(self, title: str, markdown: str, channel_id: str | None = None) -> str:
        """Canvas 하나를 만들고 ID를 반환합니다. channel_id를 주면 채널 탭으로 붙음."""
        payload: dict[str, Any] = {
            "title": title,
            "document_content": {"type": "markdown", "markdown": markdown},
        }
        if channel_id:
            payload["channel_id"] = channel_id
        data = self._post("canvases.create", payload)
        return data["canvas_id"]

    # ------- 매일 갱신 -------
    def lookup_section_by_text(self, canvas_id: str, anchor_text: str) -> str | None:
        """anchor_text가 포함된 섹션의 ID를 찾습니다."""
        data = self._post(
            "canvases.sections.lookup",
            {
                "canvas_id": canvas_id,
                "criteria": {"contains_text": anchor_text},
            },
        )
        sections = data.get("sections") or []
        if not sections:
            return None
        return sections[0]["id"]

    def replace_section(self, canvas_id: str, section_id: str, markdown: str) -> None:
        self._post(
            "canvases.edit",
            {
                "canvas_id": canvas_id,
                "changes": [
                    {
                        "operation": "replace",
                        "section_id": section_id,
                        "document_content": {"type": "markdown", "markdown": markdown},
                    }
                ],
            },
        )

    def list_sections_by_anchors(
        self,
        canvas_id: str,
        priority_anchors: list[str] | None = None,
        text_anchors: list[str] | None = None,
        include_headers: bool = True,
    ) -> list[str]:
        """우선순위가 있는 anchor 매칭으로 섹션 ID를 반환합니다.

        반환 순서:
          1. priority_anchors 매칭 (먼저 삭제됨 — 예: 표 셀 단어들)
          2. include_headers=True면 any_header 매칭
          3. text_anchors 매칭 (헤더/팀카드/푸터 등)

        같은 ID가 여러 anchor에 매칭되면 첫 등장 위치만 사용.
        호출자가 받은 list 순서대로 delete하면 표 셀이 먼저 비워져서
        Slack이 빈 표 컨테이너를 정리할 시간을 가질 수 있습니다.
        """
        seen: set[str] = set()
        ordered: list[str] = []

        def _collect(criteria: dict, label: str) -> None:
            try:
                data = self._post(
                    "canvases.sections.lookup",
                    {"canvas_id": canvas_id, "criteria": criteria},
                )
                for s in data.get("sections") or []:
                    sid = s.get("id")
                    if sid and sid not in seen:
                        seen.add(sid)
                        ordered.append(sid)
            except RuntimeError as e:
                print(f"[warn] lookup '{label}' failed: {e}")

        # 1) 우선순위 — 표 안의 단어들을 먼저
        for anchor in priority_anchors or []:
            _collect({"contains_text": anchor}, f"priority:{anchor}")

        # 2) 헤더 (H1/H2/H3)
        if include_headers:
            _collect({"section_types": ["any_header"]}, "any_header")

        # 3) 일반 본문 anchor
        for anchor in text_anchors or []:
            _collect({"contains_text": anchor}, f"text:{anchor}")

        # 4) Universal anchor — 공백 한 글자로 모든 텍스트 섹션 잡기 (Slack 실측 동작).
        # contains_text=" "는 본문에 공백이 한 번이라도 들어간 모든 섹션을 매칭.
        # 마크다운 본문은 거의 항상 공백을 포함하므로, 위 anchor에서 누락된 잔재까지
        # 마지막에 한 번 더 쓸어담는 효과.
        _collect({"contains_text": " "}, "universal:space")

        return ordered

    # 하위 호환 — 기존 호출자가 list_all_sections 그대로 쓰도록 유지
    def list_all_sections(self, canvas_id: str, text_anchors: list[str] | None = None) -> list[str]:
        return self.list_sections_by_anchors(canvas_id, text_anchors=text_anchors)

    def delete_sections(self, canvas_id: str, section_ids: list[str]) -> None:
        """주어진 섹션들을 모두 삭제합니다.

        canvases.edit는 한 호출에 changes 1개만 허용하므로, 섹션마다 따로 호출.
        """
        for sid in section_ids:
            try:
                self._post(
                    "canvases.edit",
                    {
                        "canvas_id": canvas_id,
                        "changes": [{"operation": "delete", "section_id": sid}],
                    },
                )
            except RuntimeError as e:
                # 다른 섹션 삭제로 함께 사라진 경우(404 등)는 무시
                print(f"[warn] delete section {sid} failed: {e}")

    def rename(self, canvas_id: str, title_markdown: str) -> None:
        """Canvas의 title을 갱신합니다. title_content는 markdown 포맷."""
        self._post(
            "canvases.edit",
            {
                "canvas_id": canvas_id,
                "changes": [
                    {
                        "operation": "rename",
                        "title_content": {"type": "markdown", "markdown": title_markdown},
                    }
                ],
            },
        )

    def insert_at_end(self, canvas_id: str, markdown: str) -> None:
        """본문 끝에 markdown을 삽입합니다. 비어있는 Canvas를 채울 때 사용."""
        self._post(
            "canvases.edit",
            {
                "canvas_id": canvas_id,
                "changes": [
                    {
                        "operation": "insert_at_end",
                        "document_content": {"type": "markdown", "markdown": markdown},
                    }
                ],
            },
        )
