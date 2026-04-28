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

    def replace_whole_canvas(self, canvas_id: str, markdown: str) -> None:
        """가장 단순한 fallback — 본문 전체를 한 섹션으로 갈아끼움.

        Canvas에 섹션이 하나뿐일 때 또는 lookup이 실패할 때 사용.
        """
        # canvases.edit는 한 번에 한 operation만 허용하므로
        # delete 후 insert_at_end 패턴으로 풀거나, 첫 섹션을 replace합니다.
        # 가장 안전한 방법: 첫 헤딩 섹션을 lookup해서 replace하고,
        # 그 안에 전체 내용을 다 넣는 것. 하지만 우리는 앵커 패턴을 쓰므로
        # 이 메서드는 비상용입니다.
        raise NotImplementedError("앵커 기반 섹션 교체를 사용하세요")
