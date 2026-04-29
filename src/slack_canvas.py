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

    def list_all_sections(self, canvas_id: str, text_anchors: list[str] | None = None) -> list[str]:
        """Canvas에 존재하는 (가능한 한) 모든 섹션 ID를 반환합니다.

        Slack API 제약:
        - section_types enum은 h1/h2/h3/any_header만 허용 (any_text 없음)
        - contains_text는 비어있으면 거부 (must be > 0 chars)
        - 즉 "전체 섹션을 한 번에" 받는 깔끔한 호출이 없음

        대응: any_header로 모든 헤더 섹션을 가져오고,
              본문 텍스트 섹션은 호출자가 anchor 후보들을 넘겨 따로 lookup.
              결과 ID는 set으로 합쳐 중복 제거.
        """
        ids: set[str] = set()

        # 1) 모든 헤더 섹션
        try:
            data = self._post(
                "canvases.sections.lookup",
                {
                    "canvas_id": canvas_id,
                    "criteria": {"section_types": ["any_header"]},
                },
            )
            for s in data.get("sections") or []:
                if s.get("id"):
                    ids.add(s["id"])
        except RuntimeError as e:
            print(f"[warn] header lookup failed: {e}")

        # 2) 본문 텍스트 섹션 — 호출자가 알려준 anchor 단어들로 추가 매칭
        for anchor in text_anchors or []:
            try:
                data = self._post(
                    "canvases.sections.lookup",
                    {
                        "canvas_id": canvas_id,
                        "criteria": {"contains_text": anchor},
                    },
                )
                for s in data.get("sections") or []:
                    if s.get("id"):
                        ids.add(s["id"])
            except RuntimeError as e:
                print(f"[warn] text lookup '{anchor}' failed: {e}")

        return list(ids)

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

    def debug_probe_methods(self, canvas_id: str, label: str = "") -> None:
        """[DIAG-ONLY] 다양한 canvases.* / files.* 메서드로 canvas 본문 read 가능한지 probe.

        목표: 빈 표가 어디서 오는지 이해하려면 Canvas의 실제 contents를 봐야 함.
        """
        candidates = [
            # GET 메서드처럼 정보만 반환하는 후보들
            ("files.info", "POST", {"file": canvas_id}),
            ("canvases.info", "POST", {"canvas_id": canvas_id}),
            ("canvases.get", "POST", {"canvas_id": canvas_id}),
            ("canvases.read", "POST", {"canvas_id": canvas_id}),
            ("canvases.contents", "POST", {"canvas_id": canvas_id}),
            # 더 다양한 section_types 시도 (docs에는 없지만 유효할 수도)
            ("table_section_type", "POST_LOOKUP", {"section_types": ["table"]}),
            ("list_section_type", "POST_LOOKUP", {"section_types": ["list"]}),
            ("any_section_type", "POST_LOOKUP", {"section_types": ["any"]}),
            ("paragraph_section_type", "POST_LOOKUP", {"section_types": ["paragraph"]}),
            ("text_section_type", "POST_LOOKUP", {"section_types": ["text"]}),
        ]
        for cl, op, payload in candidates:
            try:
                if op == "POST_LOOKUP":
                    data = self._post("canvases.sections.lookup", {"canvas_id": canvas_id, "criteria": payload})
                    sections = data.get("sections") or []
                    print(f"[PROBE2 {label}] {cl}: OK {len(sections)} sections", flush=True)
                else:
                    data = self._post(cl, payload)
                    print(f"[PROBE2 {label}] {cl}: OK keys={list(data.keys())[:8]}", flush=True)
            except Exception as e:
                msg = str(e)[:200]
                print(f"[PROBE2 {label}] {cl}: ERR {msg}", flush=True)

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
