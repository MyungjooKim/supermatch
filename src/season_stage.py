"""
KBO 시즌 단계 판정.

오늘 날짜 + standings/일정 API 응답 신호를 조합해 5단계 중 하나로 분류합니다.

| 단계 | 정의 | Canvas 화면 |
|------|------|-------------|
| OFFSEASON_BEFORE | 1월 ~ 시범경기 시작 전 | 작년 최종 순위 |
| PRESEASON | 시범경기 기간 (3월 초~중순) | 작년 최종 + 정규 개막 D-N |
| REGULAR_SEASON | 정규시즌 (휴식일 포함) | 경기있음→GAMES, 없음→진행 순위 |
| POSTSEASON | 정규시즌 종료 ~ 한국시리즈 끝 | 진출 5팀 + 오늘 경기 |
| OFFSEASON_AFTER | 한국시리즈 종료 ~ 12월 31일 | 올해 최종 순위 |

판정 알고리즘 (의존성 역전을 위해 SeasonFetcher protocol을 받음):
  - this_year max(games)가 None/0 → 시즌 시작 전 (캘린더로 OFFSEASON_BEFORE/PRESEASON 분기)
  - this_year max(games) >= 144 → 정규시즌 종료 (11/15 이후면 OFFSEASON_AFTER, 이전이면 POSTSEASON)
  - 그 외 → REGULAR_SEASON
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Protocol


class SeasonStage(Enum):
    OFFSEASON_BEFORE = "offseason_before"
    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    POSTSEASON = "postseason"
    OFFSEASON_AFTER = "offseason_after"


class SeasonFetcher(Protocol):
    """판정에 필요한 데이터만 추상화. 실제 구현은 RealFetcher, 시뮬레이션은 FakeFetcher."""

    def this_year_max_games(self, year: int) -> int | None:
        """해당 연도 standings의 max(gameCount). 미래 시즌은 None."""
        ...


def detect_season_stage(today: dt.date, fetcher: SeasonFetcher) -> SeasonStage:
    """5단계 시즌 판정.

    캘린더 fallback이 들어가는 경계는:
    - 3월 22일 미만 → PRESEASON (시범경기 기간으로 가정)
    - 11월 15일 이상 → 한국시리즈 종료 가정 (OFFSEASON_AFTER)
    """
    year = today.year
    max_games = fetcher.this_year_max_games(year)

    # 1) 시즌 시작 전 (standings가 비었거나 미래 시즌 400)
    if max_games in (None, 0):
        if today.month == 3 and today.day < 22:
            return SeasonStage.PRESEASON
        if today.month <= 2 or (today.month == 3 and today.day < 22):
            return SeasonStage.OFFSEASON_BEFORE
        # 1~3월이 아닌데 standings가 비었다 = 비정상이지만 OFFSEASON_BEFORE로 안전 fallback
        return SeasonStage.OFFSEASON_BEFORE

    # 2) 정규시즌 종료 (1팀이라도 144경기 마침)
    if max_games >= 144:
        if today.month >= 12 or (today.month == 11 and today.day >= 15):
            return SeasonStage.OFFSEASON_AFTER
        return SeasonStage.POSTSEASON

    # 3) 진행 중
    return SeasonStage.REGULAR_SEASON


# ============================================================
# 실제 데이터 fetcher — Naver API 호출
# ============================================================

class RealFetcher:
    """프로덕션용 SeasonFetcher 구현. naver_kbo.fetch_team_stats를 호출합니다."""

    def this_year_max_games(self, year: int) -> int | None:
        # 순환 import 회피 위해 함수 내 import
        from naver_kbo import fetch_team_stats
        try:
            stats = fetch_team_stats(year)
        except Exception:
            # 미래 시즌은 400 → None으로 신호
            return None
        if not stats:
            return 0
        return max(s.games for s in stats)
