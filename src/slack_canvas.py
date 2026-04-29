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

    def list_all_sections(self, canvas_id: str) -> list[str]:
        """Canvas에 존재하는 모든 섹션의 ID를 반환합니다.

        Slack API는 criteria.section_types에 최대 3개까지만 허용하므로
        any_header + any_text만으로 헤딩과 텍스트 섹션을 모두 잡습니다.
        앵커 누적 문제를 피하기 위해 wipe-and-refill 흐름에서 사용합니다.
        """
        data = self._post(
            "canvases.sections.lookup",
            {
                "canvas_id": canvas_id,
                "criteria": {
                    "section_types": ["any_header", "any_text"],
                },
            },
        )
        return [s["id"] for s in (data.get("sections") or []) if s.get("id")]

    def delete_sections(self, canvas_id: str, section_ids: list[str]) -> None:
        """주어진 섹션들을 한 번의 edit 호출로 모두 삭제합니다."""
        if not section_ids:
            return
        self._post(
            "canvases.edit",
            {
                "canvas_id": canvas_id,
                "changes": [
                    {"operation": "delete", "section_id": sid} for sid in section_ids
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
