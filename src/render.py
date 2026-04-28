"""
KBO 데이터를 Slack Canvas용 마크다운으로 렌더링합니다.

레이아웃:
  헤더 (날짜)
  ── 응원팀 카드 (LG / 삼성 / 롯데) ──
  ── 오늘의 KBO 전체 일정 테이블 ──
  푸터

Slack Canvas markdown은 표준 마크다운 + 이모지 + 체크박스 + 테이블을 지원하므로,
이 범위 안에서 시각적 위계를 만듭니다.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

from naver_kbo import Game, TARGET_TEAMS, TEAM_NAME, is_monday

WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

TEAM_EMOJI = {
    "LG": ":baseball:",
    "SS": ":lion_face:",
    "LT": ":seagull:",  # 사직 갈매기
    "OB": ":bear:",
    "WO": ":eagle:",
    "SK": ":ship:",
    "HT": ":tiger:",
    "HH": ":fire:",
    "NC": ":t-rex:",
    "KT": ":magic_wand:",
}

# 섹션 앵커 — canvases.sections.lookup이 이 텍스트로 섹션을 찾습니다.
# (replace operation은 section_id 기준이라, 매번 lookup → replace 흐름)
ANCHOR_HEADER = "<!-- kbo:header -->"
ANCHOR_TEAMS = "<!-- kbo:teams -->"
ANCHOR_SCHEDULE = "<!-- kbo:schedule -->"
ANCHOR_FOOTER = "<!-- kbo:footer -->"


def render_header(date: dt.date) -> str:
    weekday = WEEKDAY_KO[date.weekday()]
    return (
        f"{ANCHOR_HEADER}\n"
        f"# :baseball: 오늘의 KBO\n"
        f"### {date.year}년 {date.month}월 {date.day}일 ({weekday})\n"
    )


def render_team_card(team_code: str, game: Game | None, summary: str) -> str:
    """응원팀 한 팀의 카드를 그립니다."""
    emoji = TEAM_EMOJI.get(team_code, ":baseball:")
    name = TEAM_NAME.get(team_code, team_code)

    if game is None:
        # 경기 없는 날
        return (
            f"### {emoji} {name}\n"
            f"> _{summary}_\n"
        )

    if game.is_canceled:
        return (
            f"### {emoji} {name}\n"
            f"> 우천 등 사유로 경기 취소 ({game.stadium})\n"
        )

    opp = TEAM_NAME.get(game.opponent_of(team_code), game.opponent_of(team_code))
    my_score = game.score_for(team_code)
    opp_score = game.score_for(game.opponent_of(team_code))

    if not game.is_finished:
        # 시작 전 / 진행 중
        when = game.game_time or "TBD"
        status_label = "경기 중" if game.status == "LIVE" else f"{when} 경기 예정"
        return (
            f"### {emoji} {name}\n"
            f"**vs {opp}** · {game.stadium} · {status_label}\n"
        )

    # 결과 있음
    won = game.winner_code() == team_code
    drew = game.winner_code() is None
    if drew:
        verdict_badge = "**무**"
    elif won:
        verdict_badge = "**승**"
    else:
        verdict_badge = "**패**"

    return (
        f"### {emoji} {name} {verdict_badge}\n"
        f"**{my_score} : {opp_score}** vs {opp} · {game.stadium}\n"
        f"> {summary}\n"
    )


def render_team_section(
    games: list[Game],
    summaries: dict[str, str],
) -> str:
    """LG / 삼성 / 롯데 카드 묶음."""
    parts = [ANCHOR_TEAMS, "## :star: 우리 팀 오늘"]
    order = ["LG", "SS", "LT"]
    for code in order:
        team_game = next((g for g in games if g.involves(code)), None)
        parts.append(render_team_card(code, team_game, summaries.get(code, "")))
    return "\n".join(parts) + "\n"


def render_schedule_table(date: dt.date, games: list[Game]) -> str:
    """오늘의 KBO 전체 경기 일정 테이블."""
    parts = [ANCHOR_SCHEDULE, "## :clipboard: 오늘의 전체 일정"]

    if is_monday(date) and not games:
        parts.append(
            "> :coffee: **월요일은 정기 휴식일입니다.**  \n"
            "> 선수도, 팬도 잠시 숨을 고르는 하루.\n"
        )
        return "\n".join(parts) + "\n"

    if not games:
        parts.append("> 오늘은 예정된 경기가 없습니다.\n")
        return "\n".join(parts) + "\n"

    parts.append("| 시간 | 원정 | 점수 | 홈 | 구장 | 상태 |")
    parts.append("| :--: | :--: | :--: | :--: | :--: | :--: |")
    for g in sorted(games, key=lambda x: x.game_time or "99:99"):
        away_emoji = TEAM_EMOJI.get(g.away_code, "")
        home_emoji = TEAM_EMOJI.get(g.home_code, "")
        if g.is_canceled:
            score = "취소"
            status = "—"
        elif g.is_finished:
            score = f"**{g.away_score} : {g.home_score}**"
            status = "종료"
        elif g.status == "LIVE":
            score = f"{g.away_score or 0} : {g.home_score or 0}"
            status = "경기중"
        else:
            score = "—"
            status = "예정"
        time_label = g.game_time or "—"
        parts.append(
            f"| {time_label} | {away_emoji} {g.away_name} | {score} | "
            f"{home_emoji} {g.home_name} | {g.stadium} | {status} |"
        )
    return "\n".join(parts) + "\n"


def render_footer() -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        f"{ANCHOR_FOOTER}\n"
        f"---\n"
        f"_업데이트: {now} KST · 데이터: Naver 스포츠 · 요약: Claude_\n"
    )


def render_full_canvas(
    date: dt.date,
    games: list[Game],
    summaries: dict[str, str],
) -> str:
    """초기 Canvas 생성용 — 전체 본문을 통째로 렌더링."""
    return "\n".join([
        render_header(date),
        render_team_section(games, summaries),
        render_schedule_table(date, games),
        render_footer(),
    ])
