"""
KBO Canvas updater 메인 엔트리포인트.

사용법:
  # 최초 1회: Canvas 생성. 출력된 canvas_id를 GitHub Secrets에 저장.
  python main.py init --channel C0123456789

  # 매일 실행 (GitHub Actions):
  python main.py update
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import traceback

import anthropic

from naver_kbo import (
    Game,
    TARGET_TEAMS,
    fetch_box_score,
    fetch_schedule,
    today_kst,
)
from render import (
    ANCHOR_FOOTER,
    ANCHOR_HEADER,
    ANCHOR_SCHEDULE,
    ANCHOR_TEAMS,
    render_footer,
    render_full_canvas,
    render_header,
    render_schedule_table,
    render_team_section,
)
from slack_canvas import SlackCanvasClient
from summarize import no_game_message, summarize_game_for_team


def build_summaries(games: list[Game], claude: anthropic.Anthropic) -> dict[str, str]:
    """LG / 삼성 / 롯데 각각의 카드용 요약 문장을 만듭니다."""
    out: dict[str, str] = {}
    for code in ("LG", "SS", "LT"):
        game = next((g for g in games if g.involves(code)), None)
        if game is None:
            out[code] = no_game_message(code, claude)
            continue
        if game.is_canceled or not game.is_finished:
            out[code] = ""  # 카드 자체가 결과 대신 다른 라벨을 보여주므로 빈 문자열
            continue
        try:
            box = fetch_box_score(game.game_id)
            out[code] = summarize_game_for_team(game, box, code, claude)
        except Exception as e:
            print(f"[warn] summary failed for {code}: {e}", file=sys.stderr)
            # 폴백: 스코어만으로 무미건조하게
            opp = game.opponent_of(code)
            out[code] = f"{game.score_for(code)} - {game.score_for(opp)}로 경기를 마쳤습니다."
    return out


def cmd_init(args) -> None:
    """최초 Canvas 생성. 출력된 canvas_id를 안전한 곳에 저장하세요."""
    date = today_kst()
    games = fetch_schedule(date)

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    summaries = build_summaries(games, claude)
    markdown = render_full_canvas(date, games, summaries)

    slack = SlackCanvasClient()
    title = "오늘의 KBO :baseball:"
    canvas_id = slack.create_canvas(title, markdown, channel_id=args.channel)
    print(f"CANVAS_ID={canvas_id}")
    print("→ 이 값을 GitHub Secrets의 SLACK_CANVAS_ID에 저장하세요.")


def cmd_update(args) -> None:
    """매일 실행 — 4개 섹션을 lookup → replace로 갱신."""
    canvas_id = args.canvas_id or os.environ["SLACK_CANVAS_ID"]
    date = today_kst()
    games = fetch_schedule(date)

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    summaries = build_summaries(games, claude)

    sections = {
        ANCHOR_HEADER: render_header(date),
        ANCHOR_TEAMS: render_team_section(games, summaries),
        ANCHOR_SCHEDULE: render_schedule_table(date, games),
        ANCHOR_FOOTER: render_footer(),
    }

    slack = SlackCanvasClient()
    failures: list[str] = []
    for anchor, content in sections.items():
        try:
            section_id = slack.lookup_section_by_text(canvas_id, anchor)
            if section_id is None:
                failures.append(f"section not found for {anchor!r}")
                continue
            slack.replace_section(canvas_id, section_id, content)
            print(f"✓ updated section {anchor}")
        except Exception:
            failures.append(f"failed {anchor}: {traceback.format_exc()}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Supermatch - KBO daily Canvas updater")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="최초 Canvas 생성")
    p_init.add_argument("--channel", help="Canvas를 탭으로 붙일 채널 ID (선택)")
    p_init.set_defaults(func=cmd_init)

    p_update = sub.add_parser("update", help="기존 Canvas를 오늘자로 갱신")
    p_update.add_argument("--canvas-id", help="(선택) 환경변수 대신 직접 전달")
    p_update.set_defaults(func=cmd_update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
