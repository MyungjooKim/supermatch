"""
KBO 데이터를 마크다운으로 렌더링합니다. page.py 가 이 마크다운을 HTML로 변환합니다.

레이아웃:
  헤더 (날짜)
  ── 응원팀 카드 (LG / 삼성 / 롯데) ──
  ── 오늘의 KBO 전체 일정 ──
  푸터

이모지는 `:shortcode:` 로 쓰고, page.py의 SHORTCODE_TO_UNICODE 가 유니코드로
치환합니다. 새 shortcode를 쓰면 그 매핑에도 추가해야 합니다 (미매핑은 제거됨).
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

from naver_kbo import KST, Game, TARGET_TEAMS, TEAM_NAME, TeamStanding, is_monday

WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

TEAM_EMOJI = {
    # 응원팀 3팀 — page.py에서 유니코드 이모지로 치환됨
    "LG": ":lg_lucky:",
    "SS": ":sslion:",
    "LT": ":lotte_giant:",
    "OB": ":bear:",
    "WO": ":eagle:",
    "SK": ":ship:",
    "HT": ":tiger:",
    "HH": ":fire:",
    "NC": ":t-rex:",
    "KT": ":magic_wand:",
}

def render_header(date: dt.date) -> str:
    weekday = WEEKDAY_KO[date.weekday()]
    # H1 한 줄로 통합 — 날짜를 별도 H3로 두면 바로 아래 H2(:coffee:/팀 카드)와
    # 시각적 무게가 충돌함.
    return f"# :duck_wave01: 우리 팀 오늘 · {date.year}년 {date.month}월 {date.day}일 ({weekday})\n"


def _name_with_starter(team_name: str, starter: str) -> str:
    """팀명 옆에 선발투수 이름을 괄호로 붙입니다. starter가 비면 팀명만."""
    return f"{team_name}({starter})" if starter else team_name


def render_team_card(
    team_code: str,
    game: Game | None,
    summary: str,
    starters: dict[str, str] | None = None,
) -> str:
    """응원팀 한 팀의 카드를 그립니다.

    starters: {"home": "이름", "away": "이름"} 형식. 시작 전 경기에서 사용.
    """
    starters = starters or {}
    emoji = TEAM_EMOJI.get(team_code, ":baseball:")
    name = TEAM_NAME.get(team_code, team_code)

    # 카드 끝에 \n 두지 않음 — render_team_section의 "\n".join이
    # 카드 사이에 \n을 넣을 때 trailing \n이 합쳐지면 빈 줄(=빈 섹션)이 누적됨.
    if game is None:
        # 경기 없는 날
        return (
            f"## {emoji} {name}\n"
            f"> _{summary}_"
        )

    if game.is_canceled:
        return (
            f"## {emoji} {name}\n"
            f"> 우천 등 사유로 경기 취소 ({game.stadium})"
        )

    opp_code = game.opponent_of(team_code)
    opp = TEAM_NAME.get(opp_code, opp_code)
    my_score = game.score_for(team_code)
    opp_score = game.score_for(opp_code)

    # 선발투수 정보 (있으면 괄호로 표기)
    is_home = team_code == game.home_code
    my_starter = starters.get("home" if is_home else "away", "")
    opp_starter = starters.get("away" if is_home else "home", "")
    my_label = _name_with_starter(name, my_starter)
    opp_label = _name_with_starter(opp, opp_starter)

    if not game.is_finished:
        # 시작 전 / 진행 중
        when = game.game_time or "TBD"
        status_label = "경기 중" if game.status == "LIVE" else f"{when} 경기 예정"
        return (
            f"## {emoji} {my_label}\n"
            f"**vs {opp_label}** · {game.stadium} · {status_label}"
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
        f"## {emoji} {name} {verdict_badge}\n"
        f"**{my_score} : {opp_score}** vs {opp} · {game.stadium}\n"
        f"> {summary}"
    )


def render_team_section(
    games: list[Game],
    summaries: dict[str, str],
    starters_by_game: dict[str, dict[str, str]] | None = None,
) -> str:
    """LG / 삼성 / 롯데 카드 묶음.

    starters_by_game: {game_id: {"home": 이름, "away": 이름}} 매핑.
    """
    starters_by_game = starters_by_game or {}
    # 섹션 헤더는 제거. H1 헤더(render_header)가 "우리 팀 오늘" 역할을 함.
    parts: list[str] = []
    order = ["LG", "SS", "LT"]
    for code in order:
        team_game = next((g for g in games if g.involves(code)), None)
        starters = starters_by_game.get(team_game.game_id, {}) if team_game else {}
        parts.append(
            render_team_card(code, team_game, summaries.get(code, ""), starters)
        )
    return "\n".join(parts) + "\n"


def render_schedule_table(date: dt.date, games: list[Game]) -> str:
    """오늘의 KBO 전체 경기 일정 — 경기당 2줄 blockquote 카드."""
    parts = ["## :clipboard: 오늘의 전체 일정"]

    if is_monday(date) and not games:
        parts.append(
            "> :coffee: **월요일은 정기 휴식일입니다.**  \n"
            "> 선수도, 팬도 잠시 숨을 고르는 하루.\n"
        )
        return "\n".join(parts) + "\n"

    if not games:
        parts.append("> 오늘은 예정된 경기가 없습니다.\n")
        return "\n".join(parts) + "\n"

    # 카드 형태 — 한 경기당 2줄.
    # 1줄: 시간 + 원정팀(점수)홈팀, 응원팀은 ⭐
    # 2줄: 구장 · 상태
    for g in sorted(games, key=lambda x: x.game_time or "99:99"):
        if g.is_canceled:
            score, status = "—", "_경기 취소_"
        elif g.is_finished:
            score, status = f"**{g.away_score} : {g.home_score}**", "종료"
        elif g.status == "LIVE":
            score, status = f"**{g.away_score or 0} : {g.home_score or 0}**", "_경기 중_"
        else:
            score, status = "vs", "예정"
        time_label = g.game_time or "—"
        away_marker = "⭐" if g.away_code in TARGET_TEAMS else ""
        home_marker = "⭐" if g.home_code in TARGET_TEAMS else ""
        away_label = f"{away_marker}{g.away_name}"
        home_label = f"{home_marker}{g.home_name}"
        # blockquote (>) 로 한 경기를 시각적 묶음 처리
        # 끝에 \n을 두지 않음 — "\n".join으로 합쳐질 때 빈 줄이 생기지 않게.
        # 둘째 줄 끝에 hard break("  ")를 붙여 다음 경기 카드와 lazy
        # continuation으로 합쳐지지 않게 막음.
        parts.append(
            f"> :baseball: **{time_label}** · "
            f"{away_label} {score} {home_label}  \n"
            f"> :round_pushpin: {g.stadium} · {status}"
        )
    return "\n".join(parts) + "\n"


def render_yesterday_summary(yesterday: dt.date, summaries: dict[str, str]) -> str:
    """어제 우리 팀 경기 결과 요약 섹션.

    summaries가 비어 있으면 (결과 없음 / 경기 없음) 빈 문자열 반환.
    호출 측에서 None 체크 후 이 함수를 호출하므로, None은 여기서 처리하지 않음.
    """
    if not summaries:
        return ""

    month = yesterday.month
    day = yesterday.day
    parts = [f"## :rewind: 우리 팀 어제 경기 결과 ({month}/{day})"]
    team_lines: list[str] = []
    for code in ("LG", "SS", "LT"):
        text = summaries.get(code)
        if not text:
            continue
        emoji = TEAM_EMOJI.get(code, ":baseball:")
        name = TEAM_NAME.get(code, code)
        team_lines.append(f"> {emoji} **{name}**: {text}")
    if team_lines:
        # 각 팀 사이에 blockquote 내부 빈 줄("\n>\n")을 끼워 시각적 단락 분리.
        # 같은 blockquote 컨테이너 안의 paragraph break이라 빈 섹션은 생성되지 않음
        # (\n\n으로 컨테이너 자체가 끊기지 않게 ">"를 유지).
        parts.append("\n>\n".join(team_lines))
    return "\n".join(parts) + "\n"


def render_footer() -> str:
    # GitHub Actions runner는 UTC라서 naive now()는 UTC를 출력합니다.
    # KST로 표기하므로 KST tz를 명시합니다.
    #
    # 본문보다 시각적 무게를 낮추기 위해 italic.
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return (
        f"_자동 갱신: KST 08:07 / 17:13 / 20:17 / 23:37_  \n"
        f"_업데이트: {now} KST · 데이터: Naver 스포츠 · 요약: Claude_"
    )


def _last_five_emoji(s: str) -> str:
    """'WLLWW' → '🟢⚫⚫🟢🟢' 같은 컬러 점으로 변환."""
    mapping = {"W": "🟢", "L": "🔴", "D": "⚪", "T": "⚪"}
    return "".join(mapping.get(c, "·") for c in s)


def render_standings_table(standings: list[TeamStanding]) -> str:
    """KBO 정규시즌 팀 순위 — blockquote 한 팀 1줄 컴팩트 레이아웃.

    응원팀(LG/삼성/롯데)은 ⭐ 마커 + 최근 5경기 색깔 점으로 강조.
    """
    parts = ["## :bar_chart: KBO 팀 순위"]

    # 팀당 1줄 — 가로로 압축해 한눈에 스캔 가능. 응원팀은 같은 줄에 최근 5경기
    # 색깔 점도 붙여 "최근 5경기" 별도 박스가 필요없게 흡수.
    # 각 줄 끝에 hard break("  ")를 붙여 다음 줄과 lazy continuation 차단.
    rank_lines: list[str] = []
    for s in standings:
        marker = "⭐ " if s.team_code in TARGET_TEAMS else ""
        gb = "1위" if s.game_behind == 0.0 and s.ranking == 1 else f"{s.game_behind:.1f}G"
        recent = f" · {_last_five_emoji(s.last_five)}" if s.team_code in TARGET_TEAMS else ""
        rank_lines.append(
            f"> **{s.ranking}위 · {marker}{s.team_name}** · "
            f"승률 {s.win_rate:.3f} · {gb} · "
            f"{s.wins}승 {s.losses}패 {s.draws}무 · 연속 {s.streak}{recent}  "
        )
    parts.append("\n".join(rank_lines))

    return "\n".join(parts) + "\n"


def render_no_games_notice(date: dt.date) -> str:
    """경기 없는 날 안내. 월요일 정기 휴식과 그 외 휴식을 구분합니다."""
    if is_monday(date):
        return "## :coffee: 오늘은 KBO 휴식일 — 아래는 현재 시즌의 팀 순위입니다.\n"
    return "## :zzz: 오늘은 KBO 경기가 없습니다 — 다음 경기를 기다리며, 현재 시즌의 팀 순위를 확인해보세요.\n"


def render_full_standings(date: dt.date, standings: list[TeamStanding]) -> str:
    """경기 없는 날의 페이지 본문 — 헤더 + 휴식 안내 + 순위표 + 푸터."""
    return "\n".join([
        render_header(date),
        render_no_games_notice(date),
        render_standings_table(standings),
        render_footer(),
    ])


# ============================================================
# 시즌 단계별 화면 (Plan: docs/01-plan/supermatch-season-states.md)
# ============================================================

def render_offseason_before(date: dt.date, last_year: int, last_year_stats: list[TeamStanding]) -> str:
    """1월 ~ 시즌 시작 전: '오프시즌 — 작년(last_year) 최종 순위'."""
    notice = (
        f"## :snowflake: KBO 오프시즌\n"
        f"> {date.year}년 정규시즌은 아직 시작 전입니다. "
        f"아래는 {last_year}년 최종 순위입니다.\n"
    )
    table = render_standings_table(last_year_stats).replace(
        "## :bar_chart: KBO 팀 순위",
        f"## :bar_chart: {last_year}년 최종 순위",
    )
    return "\n".join([render_header(date), notice, table, render_footer()])


def render_preseason(date: dt.date, last_year: int, last_year_stats: list[TeamStanding]) -> str:
    """시범경기 기간: '시범경기 / 정규시즌 D-N + 작년 최종'."""
    # 정규시즌 개막은 보통 3월 22~28일 사이로 가정 — 정확한 D-day는 일정 API로 보강 가능
    notice = (
        f"## :baseball: KBO 시범경기 기간\n"
        f"> {date.year}년 정규시즌 개막을 앞두고 시범경기가 진행 중입니다. "
        f"아래는 {last_year}년 최종 순위입니다.\n"
    )
    table = render_standings_table(last_year_stats).replace(
        "## :bar_chart: KBO 팀 순위",
        f"## :bar_chart: {last_year}년 최종 순위",
    )
    return "\n".join([render_header(date), notice, table, render_footer()])


def render_offseason_after(date: dt.date, this_year: int, final_stats: list[TeamStanding]) -> str:
    """시즌 종료 후 ~ 12월: '시즌 종료 — 올해 최종 순위'."""
    notice = (
        f"## :trophy: {this_year} KBO 시즌 종료\n"
        f"> {this_year}년 KBO 시즌이 마무리되었습니다. 모든 팀과 팬들 수고 많으셨습니다.\n"
        f"> 다음 시즌까지 잠시 휴식기를 가집니다.\n"
    )
    table = render_standings_table(final_stats).replace(
        "## :bar_chart: KBO 팀 순위",
        f"## :bar_chart: {this_year}년 최종 순위",
    )
    return "\n".join([render_header(date), notice, table, render_footer()])


def render_postseason_top5(
    date: dt.date,
    games: list[Game],
    top5: list[TeamStanding],
) -> str:
    """포스트시즌: 진출 5팀 강조 + 오늘 PO 경기.

    KBO 포스트시즌은 정규시즌 1~5위가 진출:
      와일드카드(4 vs 5) → 준PO(3 vs WC승자) → PO(2 vs 준PO승자) → 한국시리즈(1 vs PO승자)
    """
    notice = (
        f"## :fire: KBO 포스트시즌 진행 중\n"
        f"> 정규시즌이 마무리되고 가을 야구가 한창입니다. "
        f"한국시리즈 진출을 향한 5팀의 여정을 응원해주세요.\n"
    )

    # 5팀 표 (응원팀 강조는 render_standings_table 그대로 활용)
    table_full = render_standings_table(top5)
    table = table_full.replace(
        "## :bar_chart: KBO 팀 순위",
        "## :star: 포스트시즌 진출 5팀",
    )

    # 오늘 PO 경기
    if games:
        schedule_section = render_schedule_table(date, games).replace(
            "## :clipboard: 오늘의 전체 일정",
            "## :clipboard: 오늘의 포스트시즌 경기",
        )
    else:
        schedule_section = (
            "## :clipboard: 오늘의 포스트시즌 경기\n"
            "> 오늘은 포스트시즌 경기가 없습니다. 다음 경기를 기다려요.\n"
        )

    return "\n".join([render_header(date), notice, table, schedule_section, render_footer()])
