"""
Naver Sports KBO data fetcher.

Naver의 비공식 내부 API를 사용합니다.
- /schedule/games  : 날짜별 경기 일정 + 결과
- /game/{gameId}/preview : 경기 상세 (선발, 라인업)
- /game/{gameId}/record  : 박스스코어 (이닝 점수, 투수/타자 기록)

비공식 API라 언제든 스펙이 바뀔 수 있으니 응답 검증을 꼼꼼히 합니다.
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
        home = g.get("homeTeamCode", "")
        away = g.get("awayTeamCode", "")
        games.append(
            Game(
                game_id=str(g.get("gameId", "")),
                game_date=g.get("gameDate", date.isoformat()),
                game_time=g.get("gameDateTime", "")[-5:] if g.get("gameDateTime") else g.get("gtime", ""),
                stadium=g.get("stadium", ""),
                home_code=home,
                away_code=away,
                home_name=TEAM_NAME.get(home, home),
                away_name=TEAM_NAME.get(away, away),
                home_score=g.get("homeTeamScore"),
                away_score=g.get("awayTeamScore"),
                status=g.get("statusCode") or g.get("gameStatusCode") or "BEFORE",
                cancel_reason=g.get("cancelFlag") and g.get("suspendedReason"),
            )
        )
    return games


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
