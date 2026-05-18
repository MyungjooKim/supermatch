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
import json
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
    render_yesterday_summary,
)
from season_stage import RealFetcher, SeasonStage, detect_season_stage
from slack_canvas import SlackCanvasClient
from summarize import no_game_message, summarize_game_for_team

# Canvas title — markdown 포맷. :baseball: shortcode가 ⚾로 변환됩니다.
# rename operation에서도 markdown으로 받기 때문에 init/update가 동일하게 사용.
CANVAS_TITLE = ":baseball: 오늘의 KBO :baseball:"

# 어제 경기 요약을 저장하는 파일 (23:37 실행이 저장, 이후 실행이 읽음)
SUMMARY_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "yesterday_summary.json")


def _load_summary_state() -> dict:
    """저장된 어제 경기 요약을 읽습니다. 없거나 파싱 실패 시 빈 dict."""
    try:
        with open(SUMMARY_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_summary_state(date: dt.date, summaries: dict[str, str]) -> None:
    """어제 경기 요약을 JSON 파일에 저장합니다."""
    os.makedirs(os.path.dirname(SUMMARY_STATE_PATH), exist_ok=True)
    payload = {"date": date.isoformat(), "summaries": summaries}
    with open(SUMMARY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ summary state saved: {date} → {list(summaries.keys())}")


def _make_score_fallback(game: "Game", code: str) -> str:
    """box score 없이 스코어+승패만으로 한 줄 요약을 만듭니다."""
    from naver_kbo import TEAM_NAME
    opp = game.opponent_of(code)
    my = game.score_for(code)
    op = game.score_for(opp)
    won = game.winner_code() == code
    drew = game.winner_code() is None
    verdict = "무승부" if drew else ("승리" if won else "패배")
    return f"{TEAM_NAME.get(opp, opp)}전 {my}-{op} {verdict}."


def _build_fresh_summaries(
    yesterday: dt.date, games: list["Game"], claude: anthropic.Anthropic
) -> dict[str, str]:
    """어제 경기를 Claude로 요약합니다. box score가 없으면 스코어 폴백."""
    out: dict[str, str] = {}
    for code in ("LG", "SS", "LT"):
        game = next((g for g in games if g.involves(code)), None)
        if game is None or not game.is_finished:
            continue
        try:
            box = fetch_box_score(game.game_id)
        except Exception as e:
            print(f"[warn] box score fetch failed for {code}: {e}", file=sys.stderr)
            box = {}
        if not box or not any(box.get(k) for k in ("scoreboard", "batters", "pitchers")):
            out[code] = _make_score_fallback(game, code)
            continue
        try:
            out[code] = summarize_game_for_team(game, box, code, claude)
        except Exception as e:
            print(f"[warn] summary failed for {code}: {e}", file=sys.stderr)
            out[code] = _make_score_fallback(game, code)
    return out


def build_yesterday_summaries(
    today: dt.date, stage: "SeasonStage", claude: anthropic.Anthropic, *, is_final_run: bool = False
) -> dict[str, str] | None:
    """어제 경기 결과 한 줄 요약을 반환합니다.

    반환값:
    - None: 섹션 자체를 숨겨야 하는 경우
    - {}: 어제 경기 데이터 없음 (섹션 표시 생략)
    - {"LG": "...", ...}: 팀별 요약

    is_final_run=True (23:37 실행): 당일 경기가 막 끝난 직후라 box score API가
    살아있음 → Claude 요약 생성 후 state 파일에 저장.
    is_final_run=False (나머지 실행): state 파일에서 읽음. 파일 없으면 스코어 폴백.
    """
    from season_stage import SeasonStage

    if stage in (SeasonStage.OFFSEASON_BEFORE, SeasonStage.PRESEASON, SeasonStage.OFFSEASON_AFTER):
        return None

    yesterday = today - dt.timedelta(days=1)

    try:
        yest_games = fetch_schedule(yesterday)
    except Exception as e:
        print(f"[warn] yesterday schedule fetch failed: {e}", file=sys.stderr)
        return {}

    if stage == SeasonStage.POSTSEASON:
        our_games = [g for g in yest_games if any(g.involves(c) for c in ("LG", "SS", "LT"))]
        if not our_games:
            return None

    if is_final_run:
        # 23:37 실행: 경기 직후라 box score 살아있음 → Claude 요약 + 저장
        out = _build_fresh_summaries(yesterday, yest_games, claude)
        if out:
            _save_summary_state(yesterday, out)
        return out

    # 08:07 / 17:13 / 20:17 실행: 저장된 파일에서 읽음
    state = _load_summary_state()
    if state.get("date") == yesterday.isoformat() and state.get("summaries"):
        print(f"✓ loaded yesterday summary from state file ({yesterday})")
        return state["summaries"]

    # 파일 없거나 날짜 불일치 → 스코어 폴백 (Claude 없이)
    print("[warn] no saved summary state, falling back to score-only", file=sys.stderr)
    return _build_fresh_summaries(yesterday, yest_games, claude)


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
        except Exception as e:
            print(f"[warn] box score fetch failed for {code}: {e}", file=sys.stderr)
            box = {}
        # box score가 비어있으면 Claude 호출 없이 스코어 폴백
        if not box or not any(box.get(k) for k in ("scoreboard", "batters", "pitchers", "etc_records")):
            out[code] = _make_score_fallback(game, code)
            continue
        try:
            out[code] = summarize_game_for_team(game, box, code, claude)
        except Exception as e:
            print(f"[warn] summary failed for {code}: {e}", file=sys.stderr)
            out[code] = _make_score_fallback(game, code)
    return out


def _is_final_run() -> bool:
    """23:37 KST 실행 여부 — 환경변수 또는 현재 시각으로 판단."""
    if os.environ.get("IS_FINAL_RUN", "").lower() in ("1", "true", "yes"):
        return True
    from naver_kbo import KST
    import datetime as dt
    now = dt.datetime.now(KST)
    # 23:30~23:59 범위를 final run으로 간주
    return now.hour == 23 and now.minute >= 30


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

    from render import (
        render_footer,
        render_header,
        render_schedule_table,
        render_team_section,
    )

    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 어제 경기 결과 요약 (None = 섹션 숨김, {} = 결과 없음)
    yesterday = date - dt.timedelta(days=1)
    final_run = _is_final_run()
    print(f"[yesterday] is_final_run={final_run}")
    yest_summaries = build_yesterday_summaries(date, stage, claude, is_final_run=final_run)
    yest_chunk = ""
    if yest_summaries is not None:
        yest_chunk = render_yesterday_summary(yesterday, yest_summaries)

    if stage == SeasonStage.POSTSEASON:
        standings = fetch_team_stats(date.year)
        top5 = standings[:5]
        po_chunk = render_postseason_top5(date, games, top5)
        if yest_chunk:
            # render_postseason_top5에 이미 footer가 포함돼 있으므로
            # footer(":wrench: 관리자 도구") 직전에 어제 요약을 삽입합니다.
            footer_anchor = ":wrench: **관리자 도구**"
            if footer_anchor in po_chunk:
                po_chunk = po_chunk.replace(footer_anchor, f"{yest_chunk}\n{footer_anchor}", 1)
            else:
                po_chunk = po_chunk + "\n" + yest_chunk
        return [po_chunk]

    # REGULAR_SEASON
    if games:
        summaries = build_summaries(games, claude)
        starters_by_game: dict[str, dict[str, str]] = {}
        for code in ("LG", "SS", "LT"):
            tg = next((g for g in games if g.involves(code)), None)
            if tg and not tg.is_finished and not tg.is_canceled:
                starters_by_game[tg.game_id] = fetch_starting_pitchers(
                    tg.game_id, tg.home_code
                )
        chunks = [
            render_header(date),
            render_team_section(games, summaries, starters_by_game),
            render_schedule_table(date, games),
        ]
        if yest_chunk:
            chunks.append(yest_chunk)
        chunks.append(render_footer())
        return chunks

    standings = fetch_team_stats(date.year)
    chunks = [render_full_standings(date, standings)]
    if yest_chunk:
        # 경기 없는 날도 어제 요약 표시 — footer 직전에 삽입
        # render_full_standings는 단일 문자열이므로 footer를 분리해 삽입
        from render import render_footer as _rf, render_header as _rh, render_no_games_notice, render_standings_table
        chunks = [
            _rh(date),
            render_no_games_notice(date),
            render_standings_table(standings),
            yest_chunk,
            _rf(),
        ]
    return chunks


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

    # 표 안에만 있는 셀 단어들 — wipe 시 가장 먼저 삭제 (사용자 관찰):
    # "기존 데이터 삭제할 때 표 안 텍스트가 먼저 사라지고, 우리 팀 카드가 사라지지만
    #  표 컨테이너는 끝까지 안 지워진다" → 표 셀 ID를 *먼저* delete 호출 큐에 넣어
    # Slack이 빈 표 컨테이너도 함께 정리하도록 시도합니다.
    table_priority_anchors = [
        "예정",          # 일정표 상태 셀
        "경기중",
        "종료",
        "취소",
        "원정",          # 일정표 헤더
        "시간",
        "점수",
        "상태",
        "홈",
        "구장",          # 헤더 + 구장명 셀
        "잠실",
        "고척",
        "사직",
        "대구",
        "광주",
        "대전",
        "창원",
        "수원",
        "인천",
    ]

    # 일반 본문 anchor — 헤더/팀카드/푸터 (표 외 영역). 표 셀이 다 비워진 후 처리.
    text_anchors = [
        "vs",            # 팀 카드 본문
        "데이터",        # 푸터
        "경기 예정",     # 팀 카드
        "트윈스",        # 팀명 (팀 카드 + 일정표)
        "라이온즈",
        "자이언츠",
        "히어로즈",
        "이글스",
        "랜더스",
        "타이거즈",
        "위즈",
        "다이노스",
        "베어스",
        ":",             # 마크다운 emoji shortcode 콜론
        # standings 화면 전용
        "순위",
        "승률",
        "게임차",
        "휴식",
        "휴식일",
        # H1 헤더
        "우리 팀",
        "KBO",
        "어제 경기 결과",  # render_yesterday_summary 섹션 앵커
        # 카드 summary / 어제 요약 본문이 blockquote 분리되며 anchor 누락 잡기
        "이닝",          # "7이닝", "8회" 등 거의 모든 요약에 등장
        "회",            # "8회", "9회" 등 회차 표현
        "실점",          # "3실점", "무실점" 등 투수 기록
        "안타",          # "결승 안타", "8안타" 등 타격 기록
        "투수",          # "패배 투수", "선발 투수" 등
        "결승",          # "결승타", "결승 안타" 등
        "승리",          # "완봉승리", "승리" 등
        "패배",          # "패배 투수", "역전패" 등
        # Brute-force: 한국어 조사/접속/짧은 단어 — 본문이 있는 거의 모든 섹션을 잡음.
        # contains_text는 짧을수록 매칭 폭이 넓어지므로 1~2자 조사를 다수 시도.
        # 빈 섹션(텍스트 0)은 여전히 못 잡지만, 텍스트 있는 잔재는 거의 100% 커버.
        "이",
        "가",
        "은",
        "는",
        "을",
        "를",
        "의",
        "에",
        "도",
        "와",
        "과",
        "로",
        "으로",
        "에서",
        "하다",
        "했다",
        "되다",
        "그",
        "이번",
        "오늘",
        "어제",
        "선발",
        "타자",
        "기록",
        "점",
        "타",
        "득점",
        "실패",
        "성공",
    ]

    slack = SlackCanvasClient()

    # 순서 주의: wipe → insert → rename.
    # 이전 코드는 rename을 가장 먼저 호출했는데, 이어지는 wipe가
    # title을 담고 있는 첫 헤더 섹션까지 함께 삭제하는 부수효과가 있었습니다.
    # rename을 가장 마지막에 호출해 wipe가 title에 영향 주지 않게 합니다.

    # 1) 잔여 섹션을 끈질기게 정리. 한 pass에서 잡지 못한 섹션이 다음 pass에선
    # 이웃 섹션이 사라지면서 새 anchor에 매칭될 수 있어 multi-pass가 효과적입니다.
    # 정렬 순서: 표 셀 단어(priority) → 헤더 → 본문 anchor.
    # 사용자 관찰에 따르면 표 셀을 먼저 비워야 빈 표 컨테이너가 따라서 정리됨.
    MAX_PASSES = 7
    for attempt in range(MAX_PASSES):
        section_ids = slack.list_sections_by_anchors(
            canvas_id,
            priority_anchors=table_priority_anchors,
            text_anchors=text_anchors,
            include_headers=True,
        )
        if not section_ids:
            print(f"✓ canvas confirmed empty after pass {attempt}")
            break
        slack.delete_sections(canvas_id, section_ids)
        print(f"✓ pass {attempt + 1}: attempted to clear {len(section_ids)} sections (table-first order)")
    else:
        leftover = slack.list_sections_by_anchors(
            canvas_id,
            priority_anchors=table_priority_anchors,
            text_anchors=text_anchors,
            include_headers=True,
        )
        print(
            f"[warn] {len(leftover)} sections remain after {MAX_PASSES} passes; "
            f"will append new content anyway",
            file=sys.stderr,
        )

    # 2) 새 본문 삽입 — 청크별로 순차 insert.
    for i, chunk in enumerate(chunks, 1):
        slack.insert_at_end(canvas_id, chunk)
        print(f"✓ inserted chunk {i}/{len(chunks)} ({len(chunk)} chars)")
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
