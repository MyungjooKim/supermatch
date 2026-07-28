"""
KBO 데일리 페이지 생성 엔트리포인트.

사용법:
  # 매일 실행 (GitHub Actions) — docs/index.html 을 오늘자로 갱신
  python main.py update

  # 로컬 확인용 — 요약 없이(ANTHROPIC_API_KEY 불필요) 생성
  python main.py update --no-summaries
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
    render_full_standings,
    render_offseason_after,
    render_offseason_before,
    render_postseason_top5,
    render_preseason,
    render_yesterday_summary,
)
from season_stage import RealFetcher, SeasonStage, detect_season_stage
from summarize import no_game_message, summarize_game_for_team

# 어제 경기 요약을 저장하는 파일 (23:37 실행이 저장, 이후 실행이 읽음)
SUMMARY_STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state", "yesterday_summary.json")

# GitHub Pages 산출물 경로 (main/docs 를 Pages 소스로 사용)
DOCS_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")


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


def build_markdown(date: dt.date, *, skip_summaries: bool = False) -> str:
    """오늘의 시즌 단계를 판정해 페이지 본문 마크다운을 반환합니다.

    skip_summaries=True: ANTHROPIC_API_KEY 없이도 돌도록 Claude 요약 생성을
    건너뛴다. 경기 카드는 요약 없이(빈 문자열), 어제 요약 섹션은 숨김.
    """
    games = fetch_schedule(date)
    stage = detect_season_stage(date, RealFetcher())
    print(f"[stage] {date} → {stage.value} (games today: {len(games)})")

    if stage in (SeasonStage.OFFSEASON_BEFORE, SeasonStage.PRESEASON):
        last_year = date.year - 1
        last_year_stats = fetch_team_stats(last_year)
        if stage == SeasonStage.PRESEASON:
            return render_preseason(date, last_year, last_year_stats)
        return render_offseason_before(date, last_year, last_year_stats)

    if stage == SeasonStage.OFFSEASON_AFTER:
        final_stats = fetch_team_stats(date.year)
        return render_offseason_after(date, date.year, final_stats)

    from render import (
        render_footer,
        render_header,
        render_schedule_table,
        render_team_section,
    )

    claude = None if skip_summaries else anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 어제 경기 결과 요약 (None = 섹션 숨김, {} = 결과 없음)
    yesterday = date - dt.timedelta(days=1)
    yest_chunk = ""
    if not skip_summaries:
        final_run = _is_final_run()
        print(f"[yesterday] is_final_run={final_run}")
        yest_summaries = build_yesterday_summaries(date, stage, claude, is_final_run=final_run)
        if yest_summaries is not None:
            yest_chunk = render_yesterday_summary(yesterday, yest_summaries)
    else:
        print("[html-only] skipping Claude summaries (no ANTHROPIC_API_KEY needed)")

    if stage == SeasonStage.POSTSEASON:
        standings = fetch_team_stats(date.year)
        po_body = render_postseason_top5(date, games, standings[:5])
        if yest_chunk:
            # render_postseason_top5에 이미 footer가 포함돼 있으므로
            # footer 직전에 어제 요약을 삽입합니다.
            footer_anchor = "_업데이트:"
            if footer_anchor in po_body:
                po_body = po_body.replace(footer_anchor, f"{yest_chunk}\n{footer_anchor}", 1)
            else:
                po_body = po_body + "\n" + yest_chunk
        return po_body

    # REGULAR_SEASON
    if games:
        summaries = (
            {code: "" for code in ("LG", "SS", "LT")}
            if skip_summaries
            else build_summaries(games, claude)
        )
        starters_by_game: dict[str, dict[str, str]] = {}
        for code in ("LG", "SS", "LT"):
            tg = next((g for g in games if g.involves(code)), None)
            if tg and not tg.is_finished and not tg.is_canceled:
                starters_by_game[tg.game_id] = fetch_starting_pitchers(
                    tg.game_id, tg.home_code
                )
        parts = [
            render_header(date),
            render_team_section(games, summaries, starters_by_game),
            render_schedule_table(date, games),
        ]
        if yest_chunk:
            parts.append(yest_chunk)
        parts.append(render_footer())
        return "\n".join(parts)

    # 경기 없는 날 — 순위표
    standings = fetch_team_stats(date.year)
    if not yest_chunk:
        return render_full_standings(date, standings)

    # 어제 요약이 있으면 footer 직전에 끼워넣기 위해 직접 조립
    from render import render_no_games_notice, render_standings_table
    return "\n".join([
        render_header(date),
        render_no_games_notice(date),
        render_standings_table(standings),
        yest_chunk,
        render_footer(),
    ])


def write_html_page(date: dt.date, markdown: str) -> None:
    """페이지 마크다운을 GitHub Pages용 HTML(docs/index.html)로 변환·저장."""
    from page import render_html_page

    html = render_html_page(date, markdown)
    os.makedirs(os.path.dirname(DOCS_HTML_PATH), exist_ok=True)
    with open(DOCS_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ wrote GitHub Pages HTML: {os.path.relpath(DOCS_HTML_PATH)}")


def cmd_update(args) -> None:
    """매일 실행 — docs/index.html 을 오늘자 내용으로 다시 씁니다.

    ANTHROPIC_API_KEY 가 없거나 --no-summaries 면 요약을 건너뜁니다.
    """
    date = today_kst()
    skip = bool(getattr(args, "no_summaries", False))
    if not skip and not os.environ.get("ANTHROPIC_API_KEY"):
        print("[html-only] ANTHROPIC_API_KEY 없음 → 요약 생략 모드로 생성")
        skip = True
    markdown = build_markdown(date, skip_summaries=skip)
    write_html_page(date, markdown)


def main() -> None:
    parser = argparse.ArgumentParser(description="Supermatch - KBO daily page updater")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_update = sub.add_parser("update", help="docs/index.html 을 오늘자로 갱신")
    p_update.add_argument(
        "--no-summaries",
        action="store_true",
        help="ANTHROPIC_API_KEY 가 있어도 Claude 요약을 건너뛰고 생성",
    )
    p_update.set_defaults(func=cmd_update)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
