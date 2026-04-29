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

import anthropic

from naver_kbo import (
    Game,
    TARGET_TEAMS,
    fetch_box_score,
    fetch_schedule,
    fetch_starting_pitchers,
    fetch_team_stats,
    today_kst,
)
from render import (
    render_full_canvas,
    render_full_standings,
    render_offseason_after,
    render_offseason_before,
    render_postseason_top5,
    render_preseason,
)
from season_stage import RealFetcher, SeasonStage, detect_season_stage
from slack_canvas import SlackCanvasClient
from summarize import no_game_message, summarize_game_for_team

# Canvas title — markdown 포맷. :baseball: shortcode가 ⚾로 변환됩니다.
# rename operation에서도 markdown으로 받기 때문에 init/update가 동일하게 사용.
CANVAS_TITLE = ":baseball: 오늘의 KBO :baseball:"


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


def build_canvas_chunks(date: dt.date) -> list[str]:
    """오늘의 시즌 단계를 판정해 본문을 여러 markdown 청크로 반환합니다.

    Slack `insert_at_end`는 큰 마크다운 한 번에 보내면 placeholder 표를
    부수효과로 만드는 quirk가 관찰됐습니다. 본문을 작은 청크들로 쪼개
    순차적으로 insert하면 각 호출이 단순해서 placeholder가 줄어들 수 있습니다.

    REGULAR_SEASON + 경기있음 케이스만 4개 청크 (header / team_section /
    schedule / footer)로 쪼개고, 나머지 stage는 단일 청크 그대로.
    """
    games = fetch_schedule(date)
    stage = detect_season_stage(date, RealFetcher())
    print(f"[stage] {date} → {stage.value} (games today: {len(games)})")

    if stage in (SeasonStage.OFFSEASON_BEFORE, SeasonStage.PRESEASON):
        last_year = date.year - 1
        last_year_stats = fetch_team_stats(last_year)
        if stage == SeasonStage.PRESEASON:
            return [render_preseason(date, last_year, last_year_stats)]
        return [render_offseason_before(date, last_year, last_year_stats)]

    if stage == SeasonStage.OFFSEASON_AFTER:
        final_stats = fetch_team_stats(date.year)
        return [render_offseason_after(date, date.year, final_stats)]

    if stage == SeasonStage.POSTSEASON:
        standings = fetch_team_stats(date.year)
        top5 = standings[:5]
        return [render_postseason_top5(date, games, top5)]

    # REGULAR_SEASON
    if games:
        from render import (
            render_footer,
            render_header,
            render_schedule_table,
            render_team_section,
        )

        claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        summaries = build_summaries(games, claude)
        # 응원팀 경기들의 선발투수 미리 fetch — 시작 전 카드에 (이름) 표기용
        starters_by_game: dict[str, dict[str, str]] = {}
        for code in ("LG", "SS", "LT"):
            tg = next((g for g in games if g.involves(code)), None)
            if tg and not tg.is_finished and not tg.is_canceled:
                starters_by_game[tg.game_id] = fetch_starting_pitchers(
                    tg.game_id, tg.home_code
                )
        return [
            render_header(date),
            render_team_section(games, summaries, starters_by_game),
            render_schedule_table(date, games),
            render_footer(),
        ]
    standings = fetch_team_stats(date.year)
    return [render_full_standings(date, standings)]


def build_canvas_markdown(date: dt.date) -> str:
    """기존 호환성용 — 청크들을 합쳐 한 string으로 반환. cmd_init에서만 사용."""
    return "\n".join(build_canvas_chunks(date))


def cmd_init(args) -> None:
    """최초 Canvas 생성. 출력된 canvas_id를 안전한 곳에 저장하세요."""
    date = today_kst()
    markdown = build_canvas_markdown(date)
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
    chunks = build_canvas_chunks(date)

    # 본문 텍스트 섹션 매칭용 anchor — 우리가 렌더링하는 본문에 자주 등장하는 단어들.
    # any_header가 잡지 못하는 본문 텍스트 섹션을 contains_text로 보완 매칭합니다.
    # anchor가 많을수록 lookup 횟수가 늘지만 누적 잔여를 더 잘 잡아냅니다.
    text_anchors = [
        "vs",            # 팀 카드 본문
        "구장",          # 일정표 헤더 행
        "데이터",        # 푸터
        "예정",          # 일정표 상태 셀 + 팀카드 — phantom table은 "예정"만 매칭됨
        "경기 예정",     # 팀 카드
        "경기중",        # 일정표
        "종료",          # 일정표
        "취소",          # 일정표
        "원정",          # 일정표 헤더
        "시간",          # 일정표 헤더
        "점수",          # 일정표 헤더
        "상태",          # 일정표 헤더
        "홈",            # 일정표 헤더 — 빠뜨리면 빈 placeholder 표가 잔존
        "잠실",          # 구장
        "고척",
        "사직",
        "대구",
        "광주",
        "대전",
        "창원",
        "수원",
        "인천",
        "트윈스",        # 팀명
        "라이온즈",
        "자이언츠",
        "히어로즈",
        "이글스",
        "랜더스",
        "타이거즈",
        "위즈",
        "다이노스",
        "베어스",
        ":",             # 마크다운 emoji shortcode 콜론은 거의 모든 본문에 등장
        # standings 화면 전용 anchor
        "순위",
        "승률",
        "게임차",
        "휴식",
        "휴식일",
        # H1 헤더 — render_header가 만드는 본문 첫 H1 섹션
        "우리 팀",
        "KBO",
    ]

    slack = SlackCanvasClient()

    # 순서 주의: wipe → insert → rename.
    # 이전 코드는 rename을 가장 먼저 호출했는데, 이어지는 wipe가
    # title을 담고 있는 첫 헤더 섹션까지 함께 삭제하는 부수효과가 있었습니다.
    # rename을 가장 마지막에 호출해 wipe가 title에 영향 주지 않게 합니다.

    # 1) 잔여 섹션을 끈질기게 정리. 한 pass에서 잡지 못한 섹션이 다음 pass에선
    # 이웃 섹션이 사라지면서 새 anchor에 매칭될 수 있어 multi-pass가 효과적입니다.
    MAX_PASSES = 5
    for attempt in range(MAX_PASSES):
        section_ids = slack.list_all_sections(canvas_id, text_anchors=text_anchors)
        if not section_ids:
            print(f"✓ canvas confirmed empty after pass {attempt}")
            break
        slack.delete_sections(canvas_id, section_ids)
        print(f"✓ pass {attempt + 1}: attempted to clear {len(section_ids)} sections")
    else:
        leftover = slack.list_all_sections(canvas_id, text_anchors=text_anchors)
        print(
            f"[warn] {len(leftover)} sections remain after {MAX_PASSES} passes; "
            f"will append new content anyway",
            file=sys.stderr,
        )

    # 2) 새 본문 삽입 — 청크별로 순차 insert.
    # [DIAG] 각 청크 insert 후 섹션 수 추적해 어느 청크에서 phantom 표가 생기는지 확인.
    def _probe_count(label: str) -> None:
        for crit_label, crit in [
            ("any_header", {"section_types": ["any_header"]}),
            ("contains_예정", {"contains_text": "예정"}),
            ("contains_홈", {"contains_text": "홈"}),
            ("contains_구장", {"contains_text": "구장"}),
        ]:
            try:
                data = slack._post(
                    "canvases.sections.lookup",
                    {"canvas_id": canvas_id, "criteria": crit},
                )
                n = len(data.get("sections") or [])
                print(f"  [DIAG {label}] {crit_label}: {n}", flush=True)
            except Exception as e:
                print(f"  [DIAG {label}] {crit_label}: ERR {str(e)[:100]}", flush=True)

    _probe_count("after-wipe")
    for i, chunk in enumerate(chunks, 1):
        slack.insert_at_end(canvas_id, chunk)
        print(f"✓ inserted chunk {i}/{len(chunks)} ({len(chunk)} chars)")
        _probe_count(f"after-chunk-{i}")
    print("✓ canvas refreshed")

    # 3) Title 갱신 — wipe/insert 이후 마지막에 호출
    try:
        slack.rename(canvas_id, CANVAS_TITLE)
        print(f"✓ title set: {CANVAS_TITLE}")
    except Exception as e:
        print(f"[warn] rename failed: {e}", file=sys.stderr)


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
