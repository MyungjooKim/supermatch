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

from naver_kbo import KST, Game, TARGET_TEAMS, TEAM_NAME, TeamStanding, is_monday

WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

TEAM_EMOJI = {
    "LG": ":two_men_holding_hands:",  # LG 트윈스 어원 — 쌍둥이
    "SS": ":lion_face:",
    # `:seagull:`은 Slack 기본 emoji가 아니어서 텍스트로 노출됨.
    # 워크스페이스에 custom `:seagull:`을 업로드하면 그걸로 바꿔도 됨.
    "LT": ":bird:",
    "OB": ":bear:",
    "WO": ":eagle:",
    "SK": ":ship:",
    "HT": ":tiger:",
    "HH": ":fire:",
    "NC": ":t-rex:",
    "KT": ":magic_wand:",
}

# 섹션 앵커 — canvases.sections.lookup이 이 텍스트로 섹션을 찾습니다.
# Slack Canvas markdown은 HTML 주석을 그대로 텍스트로 렌더링하므로,
# 보이는 헤딩 텍스트 자체를 anchor로 사용합니다.
ANCHOR_HEADER = "오늘의 KBO"
ANCHOR_TEAMS = "우리 팀 오늘"
ANCHOR_SCHEDULE = "오늘의 전체 일정"
ANCHOR_FOOTER = "데이터: Naver 스포츠"


def render_header(date: dt.date) -> str:
    weekday = WEEKDAY_KO[date.weekday()]
    return (
        f"# :billed_cap: 우리 팀 오늘\n"
        f"### {date.year}년 {date.month}월 {date.day}일 ({weekday})\n"
    )


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
            f"### {emoji} {my_label}\n"
            f"**vs {opp_label}** · {game.stadium} · {status_label}\n"
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


def _display_width(s: str) -> int:
    """East Asian Wide(한글/이모지) = 2, ASCII = 1.

    Slack 코드블록 폰트에서 한글이 ASCII 2배 폭은 아니지만,
    ASCII 보정을 추가하면 헤더와 데이터의 폭이 따로 놀아 어긋나 보입니다.
    단순한 룰을 유지하고 컬럼 폭을 넉넉히 두는 게 시각적으로 더 일관됩니다.
    """
    return sum(2 if ord(c) > 127 else 1 for c in s)


def _pad_right(s: str, target_width: int) -> str:
    """문자열 오른쪽에 공백 채워 target_width로."""
    return s + " " * max(0, target_width - _display_width(s))


def _pad_left(s: str, target_width: int) -> str:
    """문자열 왼쪽에 공백 채워 target_width로 (숫자 우측 정렬용)."""
    return " " * max(0, target_width - _display_width(s)) + s


def _pad_center(s: str, target_width: int) -> str:
    """문자열을 target_width 안에서 가운데 정렬."""
    space = max(0, target_width - _display_width(s))
    left = space // 2
    return " " * left + s + " " * (space - left)


def render_schedule_table(date: dt.date, games: list[Game]) -> str:
    """오늘의 KBO 전체 경기 일정 — monospace 코드블록.

    Slack Canvas 마크다운 표는 phantom placeholder 컨테이너가 누적되는
    quirk가 있어 (Slack API로 정리 불가능) 코드블록 + 한글 폭 보정 패딩으로
    표처럼 정렬된 효과를 냅니다.
    """
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

    # 컬럼 폭 (한글=2, ASCII=1 기준 display width).
    # 사용자 명세:
    # - 시간: 18:30 (5)
    # - 원정/홈: ⭐(2) + KIA 타이거즈 (12) = 14, 여유 두고 14
    # - 점수: 88:88 (5)
    # - 구장: 한글 4자 = 8
    # - 상태: 경기중 (6)
    W_TIME, W_TEAM, W_SCORE, W_STADIUM, W_STATUS = 5, 14, 5, 8, 6

    parts.append("```")
    # 헤더: 가운데 정렬 + | 구분자
    headers = ["시간", "원정", "점수", "홈", "구장", "상태"]
    widths = [W_TIME, W_TEAM, W_SCORE, W_TEAM, W_STADIUM, W_STATUS]
    parts.append(" | ".join(_pad_center(h, w) for h, w in zip(headers, widths)))
    # 구분선: 전체 폭 = 각 컬럼 + (구분자 ' | ' 3칸 × 5개)
    total_width = sum(widths) + 3 * (len(widths) - 1)
    parts.append("─" * total_width)

    for g in sorted(games, key=lambda x: x.game_time or "99:99"):
        if g.is_canceled:
            score, status = "취소", "—"
        elif g.is_finished:
            score, status = f"{g.away_score}:{g.home_score}", "종료"
        elif g.status == "LIVE":
            score, status = f"{g.away_score or 0}:{g.home_score or 0}", "경기중"
        else:
            score, status = "—", "예정"
        time_label = g.game_time or "—"
        away_marker = "⭐" if g.away_code in TARGET_TEAMS else "  "
        home_marker = "⭐" if g.home_code in TARGET_TEAMS else "  "
        # 팀 셀: 마커(2칸) + 팀명(좌측정렬, 나머지 폭)
        away_cell = away_marker + _pad_right(g.away_name, W_TEAM - 2)
        home_cell = home_marker + _pad_right(g.home_name, W_TEAM - 2)
        cells = [
            _pad_center(time_label, W_TIME),
            away_cell,
            _pad_center(score, W_SCORE),
            home_cell,
            _pad_center(g.stadium, W_STADIUM),
            _pad_center(status, W_STATUS),
        ]
        parts.append(" | ".join(cells))
    parts.append("```")
    return "\n".join(parts) + "\n"


# 수동 업데이트 버튼 — GitHub Actions workflow_dispatch UI 링크.
# 페이지에서 "Run workflow" 버튼을 누르면 1~2분 내 갱신됩니다.
MANUAL_UPDATE_URL = "https://github.com/MyungjooKim/supermatch/actions/workflows/update-canvas.yml"


def render_footer() -> str:
    # GitHub Actions runner는 UTC라서 naive now()는 UTC를 출력합니다.
    # KST로 표기하므로 KST tz를 명시합니다.
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return (
        f"---\n"
        f"[:arrows_counterclockwise: **지금 수동 업데이트**]({MANUAL_UPDATE_URL}) "
        f"— 클릭 후 GitHub 페이지에서 **Run workflow** 버튼 누르세요\n"
        f"\n"
        f"_업데이트: {now} KST · 데이터: Naver 스포츠 · 요약: Claude_\n"
    )


def render_full_canvas(
    date: dt.date,
    games: list[Game],
    summaries: dict[str, str],
    starters_by_game: dict[str, dict[str, str]] | None = None,
) -> str:
    """초기 Canvas 생성용 — 전체 본문을 통째로 렌더링."""
    return "\n".join([
        render_header(date),
        render_team_section(games, summaries, starters_by_game),
        render_schedule_table(date, games),
        render_footer(),
    ])


def _last_five_emoji(s: str) -> str:
    """'WLLWW' → '🟢⚫⚫🟢🟢' 같은 컬러 점으로 변환."""
    mapping = {"W": "🟢", "L": "🔴", "D": "⚪", "T": "⚪"}
    return "".join(mapping.get(c, "·") for c in s)


def render_standings_table(standings: list[TeamStanding]) -> str:
    """KBO 정규시즌 팀 순위 — monospace 코드블록.

    응원팀(LG/삼성/롯데)은 ⭐ 마커로 강조. 표 컨테이너가 아니라
    monospace 정렬을 쓰는 이유는 schedule_table 주석 참고.
    """
    parts = ["## :bar_chart: KBO 팀 순위"]

    # 컬럼 폭 (한글=2, ASCII=1 기준). 팀명 최대 = ⭐(2) + KIA 타이거즈 (12) = 14.
    W_RANK, W_TEAM, W_G, W_W, W_D, W_L = 4, 14, 4, 3, 3, 3
    W_PCT, W_GB, W_STREAK = 5, 6, 4

    parts.append("```")
    headers = ["순위", "팀", "경기", "승", "무", "패", "승률", "게임차", "연속"]
    widths = [W_RANK, W_TEAM, W_G, W_W, W_D, W_L, W_PCT, W_GB, W_STREAK]
    parts.append(" | ".join(_pad_center(h, w) for h, w in zip(headers, widths)))
    total_width = sum(widths) + 3 * (len(widths) - 1)
    parts.append("─" * total_width)

    for s in standings:
        marker = "⭐" if s.team_code in TARGET_TEAMS else "  "
        gb = "—" if s.game_behind == 0.0 and s.ranking == 1 else f"{s.game_behind:.1f}"
        team_cell = marker + _pad_right(s.team_name, W_TEAM - 2)
        cells = [
            _pad_center(str(s.ranking), W_RANK),
            team_cell,
            _pad_center(str(s.games), W_G),
            _pad_center(str(s.wins), W_W),
            _pad_center(str(s.draws), W_D),
            _pad_center(str(s.losses), W_L),
            _pad_center(f"{s.win_rate:.3f}", W_PCT),
            _pad_center(gb, W_GB),
            _pad_center(s.streak, W_STREAK),
        ]
        parts.append(" | ".join(cells))
    parts.append("```")

    # 응원팀 최근 5경기는 컬러 점으로 별도 표시 (코드블록 안에선 emoji 변환 안 됨)
    target_recents = [s for s in standings if s.team_code in TARGET_TEAMS]
    if target_recents:
        parts.append("")
        parts.append("**:star: 응원팀 최근 5경기**")
        for s in target_recents:
            parts.append(f"- {s.team_name}: {_last_five_emoji(s.last_five)}")

    return "\n".join(parts) + "\n"


def render_no_games_notice(date: dt.date) -> str:
    """경기 없는 날 안내. 월요일 정기 휴식과 그 외 휴식을 구분합니다."""
    if is_monday(date):
        return (
            "## :coffee: 오늘은 KBO 휴식일\n"
            "> 월요일은 정기 휴식일입니다. 선수도, 팬도 잠시 숨을 고르는 하루.\n"
            "> 아래는 현재 시즌의 팀 순위입니다.\n"
        )
    return (
        "## :zzz: 오늘은 KBO 경기가 없습니다\n"
        "> 다음 경기를 기다리며, 현재 시즌의 팀 순위를 확인해보세요.\n"
    )


def render_full_standings(date: dt.date, standings: list[TeamStanding]) -> str:
    """경기 없는 날의 Canvas 본문 — 헤더 + 휴식 안내 + 순위표 + 푸터."""
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
