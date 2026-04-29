"""
Naver Sports KBO data fetcher.

Naver의 비공식 내부 API를 사용합니다.
- /schedule/games  : 날짜별 경기 일정 + 결과
- /game/{gameId}/preview : 경기 상세 (선발, 라인업)
- /game/{gameId}/record  : 박스스코어 (이닝 점수, 투수/타자 기록)

비공식 API라 언제든 스펙이 바뀔 수 있으니 응답 검증을 꼼꼼히 합니다.

응답 필드 메모 (실측):
- gameDateTime: "2026-04-28T18:30:00" (ISO-8601, T 구분자)
- statusCode: "READY" / "STARTED" / "RESULT" / "CANCEL"
- reversedHomeAway: true 면 home/away 필드가 표시 우선순위로 뒤집혀 있음
- stadium은 별도 필드명을 쓰거나 응답에서 빠질 수 있음
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
BASE = "https://api-gw.sports.naver.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.sports.naver.com/",
    "Accept": "application/json, text/plain, */*",
}

# Naver의 팀 코드 ↔ 한글 팀명 매핑 (KBO 10개 구단)
TEAM_NAME = {
    "LG": "LG 트윈스",
    "OB": "두산 베어스",
    "WO": "키움 히어로즈",
    "SK": "SSG 랜더스",
    "LT": "롯데 자이언츠",
    "SS": "삼성 라이온즈",
    "HT": "KIA 타이거즈",
    "HH": "한화 이글스",
    "NC": "NC 다이노스",
    "KT": "KT 위즈",
}

# 팀 코드 → 홈구장 (KBO는 1팀 1홈구장 원칙. 잠실은 LG/두산 공동홈)
HOME_STADIUM = {
    "LG": "잠실",
    "OB": "잠실",      # 두산
    "WO": "고척",      # 키움
    "SK": "인천",      # SSG (랜더스필드)
    "LT": "사직",      # 롯데
    "SS": "대구",      # 삼성 (라이온즈파크)
    "HT": "광주",      # KIA (챔피언스필드)
    "HH": "대전",      # 한화 (이글스파크)
    "NC": "창원",      # NC (NC파크)
    "KT": "수원",      # KT (위즈파크)
}

# Naver의 statusCode → 우리 내부 상태 매핑
STATUS_MAP = {
    "READY": "BEFORE",
    "BEFORE": "BEFORE",
    "STARTED": "LIVE",
    "LIVE": "LIVE",
    "RESULT": "RESULT",
    "END": "RESULT",
    "CANCEL": "CANCEL",
    "POSTPONED": "CANCEL",
    "SUSPENDED": "CANCEL",
}

# 우리가 추적하는 응원 팀들
TARGET_TEAMS = {"LG", "SS", "LT"}  # LG, 삼성, 롯데


@dataclass
class Game:
    game_id: str
    game_date: str          # YYYY-MM-DD
    game_time: str          # HH:MM (KST)
    stadium: str
    home_code: str
    away_code: str
    home_name: str
    away_name: str
    home_score: int | None = None
    away_score: int | None = None
    status: str = "BEFORE"  # BEFORE / LIVE / RESULT / CANCEL
    cancel_reason: str | None = None
    box: dict[str, Any] = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        return self.status == "RESULT"

    @property
    def is_canceled(self) -> bool:
        return self.status == "CANCEL"

    def involves(self, team_code: str) -> bool:
        return team_code in (self.home_code, self.away_code)

    def winner_code(self) -> str | None:
        if not self.is_finished or self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return self.home_code
        if self.away_score > self.home_score:
            return self.away_code
        return None  # 무승부

    def opponent_of(self, team_code: str) -> str:
        return self.away_code if team_code == self.home_code else self.home_code

    def score_for(self, team_code: str) -> int | None:
        if self.home_score is None:
            return None
        return self.home_score if team_code == self.home_code else self.away_score


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


def _parse_game_time(raw: dict[str, Any]) -> str:
    """Naver의 시간 필드들을 HH:MM으로 정규화합니다.

    우선순위:
    1) gameDateTime: "2026-04-28T18:30:00" → "18:30"
    2) gtime / gameTime: "18:30" 또는 "18:30:00" → "18:30"
    """
    raw_dt = raw.get("gameDateTime") or ""
    if "T" in raw_dt:
        # ISO 형식: T 다음의 HH:MM만 추출
        time_part = raw_dt.split("T", 1)[1]
        return time_part[:5]  # "18:30:00" → "18:30"

    fallback = raw.get("gtime") or raw.get("gameTime") or ""
    return fallback[:5]


def _resolve_stadium(raw: dict[str, Any], home_code: str) -> str:
    """경기장 이름.

    Naver의 일정 API에는 stadium 필드가 없으므로,
    홈팀 코드로부터 홈구장을 룩업합니다.
    혹시 응답에 stadium이 들어오는 경우(중립경기 등)에는 그걸 우선합니다.
    """
    return (
        raw.get("stadium")
        or raw.get("stadiumName")
        or raw.get("ballparkName")
        or raw.get("place")
        or HOME_STADIUM.get(home_code, "")
    )


def fetch_schedule(date: dt.date) -> list[Game]:
    """해당 날짜의 KBO 경기 일정을 가져옵니다."""
    url = f"{BASE}/schedule/games"
    params = {
        "fromDate": date.isoformat(),
        "toDate": date.isoformat(),
        "categoryId": "kbo",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    games_raw = (payload.get("result") or {}).get("games") or []
    games: list[Game] = []
    for g in games_raw:
        # Naver는 reversedHomeAway=True일 때 home/away 필드를 표시 우선순위로 뒤집어 둡니다.
        # 우리는 항상 "실제 홈/원정" 기준으로 저장합니다.
        reversed_ha = bool(g.get("reversedHomeAway"))

        api_home_code = g.get("homeTeamCode", "")
        api_away_code = g.get("awayTeamCode", "")
        api_home_score = g.get("homeTeamScore")
        api_away_score = g.get("awayTeamScore")

        if reversed_ha:
            home_code, away_code = api_away_code, api_home_code
            home_score, away_score = api_away_score, api_home_score
        else:
            home_code, away_code = api_home_code, api_away_code
            home_score, away_score = api_home_score, api_away_score

        raw_status = g.get("statusCode") or g.get("gameStatusCode") or "READY"
        status = STATUS_MAP.get(raw_status, "BEFORE")
        if g.get("cancel") or g.get("cancelFlag") or g.get("suspended"):
            status = "CANCEL"

        games.append(
            Game(
                game_id=str(g.get("gameId", "")),
                game_date=g.get("gameDate", date.isoformat()),
                game_time=_parse_game_time(g),
                stadium=_resolve_stadium(g, home_code),
                home_code=home_code,
                away_code=away_code,
                home_name=TEAM_NAME.get(home_code, home_code),
                away_name=TEAM_NAME.get(away_code, away_code),
                home_score=home_score,
                away_score=away_score,
                status=status,
                cancel_reason=g.get("statusInfo") if status == "CANCEL" else None,
            )
        )
    return games


def fetch_starting_pitchers(game_id: str, true_home_code: str) -> dict[str, str]:
    """경기의 선발투수 이름을 *실제* home/away 기준으로 반환.

    Args:
        game_id: 경기 ID
        true_home_code: 우리가 정규화한 실제 홈팀 코드 (Game.home_code).
            preview 응답의 hCode가 이것과 다르면 home/away가 뒤집혀 있는 것이므로 swap.

    Returns: {"home": "투수명", "away": "투수명"} — 발표 안 된 쪽은 키 없음.

    Naver preview 응답의 homeStarter/awayStarter는 표시 우선순위 기준이라
    실제 홈/원정과 뒤집혀 있을 수 있습니다 (schedule API의 reversedHomeAway와 동일).
    """
    url = f"{BASE}/schedule/games/{game_id}/preview"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}
        pd = (resp.json().get("result") or {}).get("previewData") or {}
    except Exception:
        return {}

    gi = pd.get("gameInfo") or {}
    api_home_code = gi.get("hCode") or ""

    api_home_name = ((pd.get("homeStarter") or {}).get("playerInfo") or {}).get("name") or ""
    api_away_name = ((pd.get("awayStarter") or {}).get("playerInfo") or {}).get("name") or ""

    # preview의 hCode가 우리 정규화된 home과 다르면 뒤집혀 있는 것
    if api_home_code and api_home_code != true_home_code:
        real_home, real_away = api_away_name, api_home_name
    else:
        real_home, real_away = api_home_name, api_away_name

    out: dict[str, str] = {}
    if real_home:
        out["home"] = real_home
    if real_away:
        out["away"] = real_away
    return out


def fetch_box_score(game_id: str) -> dict[str, Any]:
    """경기의 박스스코어 (이닝별 점수, 투수/타자 기록)를 가져옵니다.

    Claude API에 요약 요청할 때 컨텍스트로 들어갑니다.
    """
    url = f"{BASE}/game/{game_id}/record"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        return {}
    data = resp.json().get("result") or {}

    # 응답에서 요약에 유용한 필드만 추려서 반환
    return {
        "scoreboard": data.get("scoreBoard") or data.get("scoreboards") or [],
        "batters": data.get("batters") or [],
        "pitchers": data.get("pitchers") or [],
        "etc": {
            "winning_pitcher": data.get("wPitcherName"),
            "losing_pitcher": data.get("lPitcherName"),
            "save_pitcher": data.get("sPitcherName"),
            "homeruns": data.get("homeRuns"),
        },
    }


def is_monday(date: dt.date) -> bool:
    return date.weekday() == 0  # 0 = Monday


@dataclass
class TeamStanding:
    """KBO 정규시즌 팀 순위 한 행."""
    team_code: str          # "LG", "KT" 등
    team_name: str          # "LG 트윈스" — TEAM_NAME 매핑 적용
    ranking: int            # 1~10
    games: int              # gameCount
    wins: int               # winGameCount
    losses: int             # loseGameCount
    draws: int              # drawnGameCount
    win_rate: float         # wra (0.692 등)
    game_behind: float      # gameBehind (0.0 / 1.5 등)
    streak: str             # continuousGameResult ("2승" / "1패" 등)
    last_five: str          # lastFiveGames ("WLLWW" 등 5글자)
    batting_avg: float | None  # offenseHra
    era: float | None       # defenseEra


def fetch_team_stats(year: int) -> list[TeamStanding]:
    """해당 연도의 KBO 정규시즌 팀 순위(승률 내림차순 10팀)를 가져옵니다.

    seasonCode=year, gameType=REGULAR_SEASON으로 호출하면
    응답 result.seasonTeamStats가 ranking 오름차순으로 정렬되어 옵니다.
    """
    url = f"{BASE}/statistics/categories/kbo/seasons/{year}/teams"
    params = {"gameType": "REGULAR_SEASON"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    rows = (payload.get("result") or {}).get("seasonTeamStats") or []

    out: list[TeamStanding] = []
    for r in rows:
        code = r.get("teamId", "")
        out.append(
            TeamStanding(
                team_code=code,
                team_name=TEAM_NAME.get(code, r.get("teamName") or code),
                ranking=int(r.get("ranking") or 0),
                games=int(r.get("gameCount") or 0),
                wins=int(r.get("winGameCount") or 0),
                losses=int(r.get("loseGameCount") or 0),
                draws=int(r.get("drawnGameCount") or 0),
                win_rate=float(r.get("wra") or 0.0),
                game_behind=float(r.get("gameBehind") or 0.0),
                streak=str(r.get("continuousGameResult") or "—"),
                last_five=str(r.get("lastFiveGames") or ""),
                batting_avg=r.get("offenseHra"),
                era=r.get("defenseEra"),
            )
        )
    # ranking 기준 정렬 보장 (응답이 이미 정렬되어 있지만 방어적으로)
    out.sort(key=lambda s: s.ranking)
    return out
