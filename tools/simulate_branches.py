"""
시즌 단계 분기 시뮬레이터.

Plan: docs/01-plan/supermatch-season-states.md
실제 src/season_stage.detect_season_stage를 import해 11개 회귀 케이스를 검증합니다.

사용법:
    PYTHONPATH=src python3 tools/simulate_branches.py

종료 코드: 모든 케이스 통과 시 0, 하나라도 실패 시 1 (CI 통합용)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from season_stage import SeasonFetcher, SeasonStage, detect_season_stage


@dataclass
class FakeFetcherResult:
    """시뮬레이션용 mock 응답 모음."""
    today_games_count: int = 0
    this_year_max_games: int | None = 0
    last_year_max_games: int = 144


class FakeFetcher:
    """SeasonFetcher protocol 구현 — fixture 값을 그대로 반환."""

    def __init__(self, result: FakeFetcherResult) -> None:
        self._result = result

    def this_year_max_games(self, year: int) -> int | None:
        return self._result.this_year_max_games


# ============================================================
# 시뮬레이션 케이스 (Plan 6장의 11개 시나리오)
# ============================================================

@dataclass
class Case:
    label: str
    today: dt.date
    fetcher: FakeFetcherResult
    expected_stage: SeasonStage
    expected_screen: str  # 사람이 읽기용 — 화면 종류


CASES: list[Case] = [
    Case(
        "2026-01-15 (목, 비시즌 한복판)",
        dt.date(2026, 1, 15),
        FakeFetcherResult(today_games_count=0, this_year_max_games=None, last_year_max_games=144),
        SeasonStage.OFFSEASON_BEFORE,
        "작년(2025) 최종 순위",
    ),
    Case(
        "2026-03-15 (일, 시범경기 기간 추정)",
        dt.date(2026, 3, 15),
        FakeFetcherResult(today_games_count=0, this_year_max_games=0, last_year_max_games=144),
        SeasonStage.PRESEASON,
        "작년 최종 순위 + 정규 개막 D-N",
    ),
    Case(
        "2026-03-28 (토, 개막 추정)",
        dt.date(2026, 3, 28),
        FakeFetcherResult(today_games_count=5, this_year_max_games=1, last_year_max_games=144),
        SeasonStage.REGULAR_SEASON,
        "GAMES (오늘의 KBO)",
    ),
    Case(
        "2026-04-29 (수, today, 시즌 진행 중)",
        dt.date(2026, 4, 29),
        FakeFetcherResult(today_games_count=5, this_year_max_games=26, last_year_max_games=144),
        SeasonStage.REGULAR_SEASON,
        "GAMES",
    ),
    Case(
        "2026-04-27 (월, 시즌중 정기 휴식)",
        dt.date(2026, 4, 27),
        FakeFetcherResult(today_games_count=0, this_year_max_games=25, last_year_max_games=144),
        SeasonStage.REGULAR_SEASON,
        "휴식일 — 진행 중 순위",
    ),
    Case(
        "2026-07-13 (월, 올스타 브레이크 추정)",
        dt.date(2026, 7, 13),
        FakeFetcherResult(today_games_count=0, this_year_max_games=80, last_year_max_games=144),
        SeasonStage.REGULAR_SEASON,
        "휴식일 — 진행 중 순위",
    ),
    Case(
        "2026-10-15 (목, 정규시즌 종료 직후 PO 시작)",
        dt.date(2026, 10, 15),
        FakeFetcherResult(today_games_count=1, this_year_max_games=144, last_year_max_games=144),
        SeasonStage.POSTSEASON,
        "포스트시즌 — 5팀 + 오늘 경기",
    ),
    Case(
        "2026-10-30 (금, 한국시리즈 진행 중)",
        dt.date(2026, 10, 30),
        FakeFetcherResult(today_games_count=1, this_year_max_games=144, last_year_max_games=144),
        SeasonStage.POSTSEASON,
        "포스트시즌 — 5팀 + 오늘",
    ),
    Case(
        "2026-11-20 (금, 시즌 종료)",
        dt.date(2026, 11, 20),
        FakeFetcherResult(today_games_count=0, this_year_max_games=144, last_year_max_games=144),
        SeasonStage.OFFSEASON_AFTER,
        "올해 최종 순위",
    ),
    Case(
        "2026-12-25 (금, 비시즌 한복판)",
        dt.date(2026, 12, 25),
        FakeFetcherResult(today_games_count=0, this_year_max_games=144, last_year_max_games=144),
        SeasonStage.OFFSEASON_AFTER,
        "올해 최종 순위",
    ),
    Case(
        "2027-01-15 (목, 미래 시즌 시작 전)",
        dt.date(2027, 1, 15),
        FakeFetcherResult(today_games_count=0, this_year_max_games=None, last_year_max_games=144),
        SeasonStage.OFFSEASON_BEFORE,
        "작년(2026) 최종 순위",
    ),
]


# ============================================================
# 러너
# ============================================================

def main() -> int:
    print(f"{'#':<3}{'케이스':<48}{'기대':<22}{'실제':<22}{'결과':<6}")
    print("-" * 110)
    pass_count = 0
    for i, c in enumerate(CASES, 1):
        actual = detect_season_stage(c.today, FakeFetcher(c.fetcher))
        ok = actual == c.expected_stage
        if ok:
            pass_count += 1
        mark = "✓" if ok else "✗"
        print(
            f"{i:<3}{c.label:<48}{c.expected_stage.value:<22}{actual.value:<22}{mark}"
        )
        if not ok:
            print(f"    화면: {c.expected_screen}")
            print(f"    fetcher: {c.fetcher}")
    print("-" * 110)
    rate = pass_count / len(CASES) * 100
    print(f"통과: {pass_count}/{len(CASES)} ({rate:.0f}%)")
    return 0 if pass_count == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
