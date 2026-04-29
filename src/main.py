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
import os
import sys

import anthropic

from naver_kbo import (
    Game,
    TARGET_TEAMS,
    fetch_box_score,
    fetch_schedule,
    today_kst,
)
from render import render_full_canvas
from slack_canvas import SlackCanvasClient
from summarize import no_game_message, summarize_game_for_team

# Canvas title — markdown 포맷. :baseball: shortcode가 ⚾로 변환됩니다.
# rename operation에서도 markdown으로 받기 때문에 init/update가 동일하게 사용.
CANVAS_TITLE = "오늘의 KBO :baseball:"


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
    canvas_id = slack.create_canvas(CANVAS_TITLE, markdown, channel_id=args.channel)
    print(f"CANVAS_ID={canvas_id}")
    print("→ 이 값을 GitHub Secrets의 SLACK_CANVAS_ID에 저장하세요.")


def cmd_update(args) -> None:
    """매일 실행 — Canvas 본문을 통째로 비우고 오늘자로 다시 채웁니다.

    이전 구현은 anchor 텍스트로 섹션을 lookup해서 replace 했지만,
    Slack의 contains_text 매칭이 여러 섹션을 잡으면서 잔여 섹션이 누적됐습니다.
    wipe-and-refill로 바꿔 매 실행마다 정확히 1세트만 보이도록 합니다.

    Slack API는 "모든 섹션 한 번에 가져오기"를 지원하지 않아,
    any_header + 본문에 자주 등장하는 단어들을 anchor로 여러 번 lookup해
    가능한 모든 섹션을 모은 뒤 일괄 삭제합니다. 삭제 후에도 잔여 섹션이
    남았는지 한 번 더 확인해 정리합니다.
    """
    canvas_id = args.canvas_id or os.environ["SLACK_CANVAS_ID"]
    date = today_kst()
    games = fetch_schedule(date)

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    summaries = build_summaries(games, claude)
    markdown = render_full_canvas(date, games, summaries)

    # 본문 텍스트 섹션 매칭용 anchor — 우리가 렌더링하는 본문에 항상 등장하는 단어들
    text_anchors = [
        "vs",          # 팀 카드 본문 ("vs KT 위즈 · 잠실 · ...")
        "구장",        # 일정표 헤더 행
        "데이터",      # 푸터
        "경기 예정",   # 팀 카드
        "경기중",      # 일정표
        "종료",        # 일정표
        "취소",        # 일정표
    ]

    slack = SlackCanvasClient()

    # Title 갱신 — Canvas가 어떻게 만들어졌든(직접 생성/init 명령) 매번 보장.
    try:
        slack.rename(canvas_id, CANVAS_TITLE)
        print(f"✓ title set: {CANVAS_TITLE}")
    except Exception as e:
        print(f"[warn] rename failed: {e}", file=sys.stderr)

    for attempt in range(3):
        section_ids = slack.list_all_sections(canvas_id, text_anchors=text_anchors)
        if not section_ids:
            break
        slack.delete_sections(canvas_id, section_ids)
        print(f"✓ pass {attempt + 1}: cleared {len(section_ids)} sections")
    slack.insert_at_end(canvas_id, markdown)
    print("✓ canvas refreshed")


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
