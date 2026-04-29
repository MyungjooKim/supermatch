# Plan: Season-Aware Canvas Updater

작성일: 2026-04-29
대상: Supermatch — KBO 일일 Slack Canvas 업데이터

## Executive Summary

| 항목 | 내용 |
|------|------|
| Feature | 시즌 단계 판정 + 단계별 화면 분기 (요건 1~4 통합) |
| Start | 2026-04-29 |
| Target | 2026 시즌 내 모든 날짜에서 정상 동작 + 장기적 회귀 방지 |
| 목표 | 비시즌·시즌중 휴식·정규·포스트시즌·시즌종료 5단계 분기 |
| Match Rate 목표 | ≥ 90% (시뮬레이터 통과율) |

### Value Delivered (4-perspective)

| Perspective | 내용 |
|-------------|------|
| Problem | 현 코드는 "오늘 경기 있음/없음" 2분기. 비시즌(1~3월)에 standings API가 빈 응답 또는 미래 시즌은 400으로 워크플로우 죽음. 포스트시즌·시즌 종료 후엔 의미 다른 데이터를 같은 화면으로 보여줌. |
| Solution | 시즌 단계 판정 함수 + 5분기 렌더 파이프라인. 외부 데이터 신호(일정 API + standings API)와 캘린더 fallback 결합. |
| Function UX | 모든 날짜에서 의미 있는 화면이 보장됨. 1~3월엔 "오프시즌 — 작년 최종 순위", 11~12월엔 "시즌 종료 — 올해 최종 순위", 10월 후반엔 "포스트시즌 진행 중 — 상위 5팀" 등 명확한 컨텍스트 표시. |
| Core Value | 365일 깨지지 않는 데일리 브리핑. 운영자(=프로젝트 오너)가 매일 확인할 필요 없음. |

---

## 1. 요건 정리 (사용자 4가지 요청)

| ID | 요건 | 단계 |
|----|------|------|
| R1 | 야구 없는 날: 해당 연도 현재 시점 KBO 순위 표시 | 시즌중 휴식 |
| R2 | 야구 있는 날: 오늘의 KBO 정보 (현 화면 유지) | 정규/포스트시즌 진행일 |
| R3 | 정규시즌 종료 후~한국시리즈 끝까지: 한국시리즈 진출 5팀 표시 | 포스트시즌 |
| R4 | 시즌 종료 후: 해당 연도 최종 순위 | 시즌 종료~연말 |

추가로 명시되진 않았지만 자명한 요건:
- R5: 비시즌(1~3월): 작년 최종 순위 표시 (R1을 그 시점에 적용하면 빈 데이터가 나오므로)

---

## 2. 시즌 단계 (5단계)

날짜 + API 응답 신호로 판정합니다.

| 단계 | 정의 | 화면 |
|------|------|------|
| **OFFSEASON_BEFORE** | 1월 1일 ~ 시범경기 시작 전 | "오프시즌" + 작년 최종 순위 |
| **PRESEASON** | 시범경기 기간 (보통 3월 초~중순) | "시범경기 / 정규시즌 D-N" + 작년 최종 순위 (또는 시범경기 일정) |
| **REGULAR_SEASON** | 정규시즌 개막 ~ 정규시즌 종료 | 경기있음→GAMES, 경기없음→올해 진행 순위 |
| **POSTSEASON** | 정규시즌 종료 다음날 ~ 한국시리즈 종료 | 상위 5팀 표시 + 진행 중인 시리즈 일정 |
| **OFFSEASON_AFTER** | 한국시리즈 종료 다음날 ~ 12월 31일 | "시즌 종료" + 올해 최종 순위 |

---

## 3. 시즌 단계 판정 알고리즘

### 데이터 소스

| 신호 | API | 신뢰도 |
|------|-----|--------|
| **오늘 경기 존재** | `fetch_schedule(today)` | 높음 (기존 코드 검증됨) |
| **시즌 전체 경기 수** | `fetch_schedule(year-01-01, year-12-31)` 의 `gameTotalCount` | 높음 (size 제한과 무관하게 메타만 받음) |
| **standings 응답 유효성** | `fetch_team_stats(year)` 의 `seasonTeamStats[0].gameCount > 0` | 높음 |
| **standings 호출 가능 여부** | 200 vs 400 | 명확 (미래 시즌은 400) |

### 판정 로직 (의사코드)

```
def detect_season_stage(today: date) -> SeasonStage:
    year = today.year
    today_games = fetch_schedule(today)

    if today_games:
        # 정규/포스트시즌 진행 중 — 어느 쪽인지 추가 판정
        if is_postseason_window(today, year):
            return POSTSEASON
        return REGULAR_SEASON

    # 오늘 경기 없음 — 시즌 단계 추가 판정
    try:
        stats_this_year = fetch_team_stats(year)
        max_games_played = max((s.games for s in stats_this_year), default=0)
    except HTTPError:
        max_games_played = 0  # 미래 시즌 400

    if max_games_played == 0:
        # 올해 시즌이 아직 시작 안 됨
        return OFFSEASON_BEFORE  # 작년 데이터로 fallback
    
    if max_games_played >= 144:
        # 정규시즌 종료 (1팀당 144경기 끝)
        if is_korean_series_finished(today, year):
            return OFFSEASON_AFTER
        return POSTSEASON
    
    # 시즌 중인데 오늘 경기만 없음 (월요일/우천 등)
    return REGULAR_SEASON


def is_postseason_window(today: date, year: int) -> bool:
    """오늘이 포스트시즌 기간인지 — 정규시즌 모든 팀이 144경기 마쳤는지로 판정.
    
    정규시즌 개막은 보통 3월 말, 종료는 10월 초. 이걸 정확히 알아낼 API가 없으므로
    standings의 gameCount 합계로 판정.
    - 합계 > 720 (10팀 × 144 / 2) → 절반 이상 진행 = 정규시즌
    - max(games) == 144 → 1팀이라도 144경기 마침 = 정규시즌 종료 → 포스트시즌
    """
    try:
        stats = fetch_team_stats(year)
        max_games = max((s.games for s in stats), default=0)
        return max_games >= 144
    except Exception:
        return False


def is_korean_series_finished(today: date, year: int) -> bool:
    """한국시리즈가 끝났는지 판정.
    
    포스트시즌 일정 API에서 마지막 경기 날짜가 today보다 과거이고,
    그 결과(winner)가 결정되어 있으면 종료.
    또는 단순 캘린더 fallback: 11월 15일 이후면 거의 확실히 종료.
    """
    if today.month >= 11 and today.day >= 15:
        return True
    # TODO: 더 정확한 판정 — postseason 일정 API 호출
    return False
```

### Fallback 캘린더 (API 신호 부족 시)

| 월 | 가정 단계 |
|----|----------|
| 1, 2 | OFFSEASON_BEFORE |
| 3 (1~중순) | PRESEASON |
| 3 (말) ~ 9 | REGULAR_SEASON |
| 10 | REGULAR_SEASON 후반 → POSTSEASON 전환 |
| 11 (1~14) | POSTSEASON |
| 11 (15~) ~ 12 | OFFSEASON_AFTER |

API 우선 + 실패 시 캘린더로 fallback.

---

## 4. 화면별 렌더 매핑

| 단계 | 오늘 경기 | 화면 | 데이터 |
|------|---------|------|--------|
| OFFSEASON_BEFORE | 없음 | "오프시즌 — 작년 최종 순위" | `fetch_team_stats(year-1)` |
| PRESEASON | 있을 수도 (시범경기) | 작년 최종 순위 + "정규 개막 D-N" | 동일 |
| REGULAR_SEASON | 있음 | 기존 GAMES 화면 | 기존 |
| REGULAR_SEASON | 없음 (월·우천) | "휴식일 — 진행 중 순위" | `fetch_team_stats(year)` |
| POSTSEASON | 있음 | "포스트시즌 — 진출 5팀 + 오늘 경기" | `fetch_team_stats(year)` 상위 5 + 일정 |
| POSTSEASON | 없음 | "포스트시즌 휴식 — 진출 5팀" | 상위 5 |
| OFFSEASON_AFTER | 없음 | "시즌 종료 — 올해 최종 순위" | `fetch_team_stats(year)` |

### 응원팀 강조

모든 standings 화면에서 LG/SS/LT는 굵게 + ⭐ 표시 유지 (Step A에서 구현 완료).

---

## 5. 구현 변경 (코드 단위)

### 5.1 신규 모듈

**`src/season_stage.py`**:
- `class SeasonStage(Enum)`: `OFFSEASON_BEFORE / PRESEASON / REGULAR_SEASON / POSTSEASON / OFFSEASON_AFTER`
- `def detect_season_stage(today: date, fetcher: SeasonFetcher) -> SeasonStage`
- `class SeasonFetcher(Protocol)`: 의존성 역전. 시뮬레이터에서 mock 주입.
  - `today_games(date) -> list[Game]`
  - `team_stats(year) -> list[TeamStanding] | None`  (None이면 400/미래)

### 5.2 기존 코드 수정

**`src/main.py` `cmd_update`**:
```python
stage = detect_season_stage(date, RealFetcher())
if stage == REGULAR_SEASON and games:
    markdown = render_full_canvas(date, games, summaries)
elif stage == REGULAR_SEASON:
    markdown = render_full_standings(date, fetch_team_stats(date.year))
elif stage in (OFFSEASON_BEFORE, PRESEASON):
    last_year_stats = fetch_team_stats(date.year - 1)
    markdown = render_offseason_before(date, last_year_stats)
elif stage == POSTSEASON:
    standings = fetch_team_stats(date.year)
    markdown = render_postseason(date, games, standings[:5])
elif stage == OFFSEASON_AFTER:
    final_stats = fetch_team_stats(date.year)
    markdown = render_offseason_after(date, final_stats)
```

**`src/render.py`** 신규 함수:
- `render_offseason_before(date, last_year_stats)` — 작년 최종 + "올해 개막 안내"
- `render_offseason_after(date, final_stats)` — 올해 최종 순위
- `render_postseason(date, games, top5)` — 포스트시즌 진출 5팀 강조 + 오늘 경기

### 5.3 시뮬레이터

**`tools/simulate_branches.py`**: 임의 날짜 + 모킹된 응답으로 분기 검증.

---

## 6. 시뮬레이션 시나리오 (회귀 케이스)

| # | 날짜 (시뮬) | 오늘 경기 | this-year stats | last-year stats | 기대 단계 | 기대 화면 |
|---|------------|---------|----------------|----------------|----------|----------|
| 1 | 2026-01-15 (목) | 0 | 미래(400) | 144경기 종료 | OFFSEASON_BEFORE | 작년 최종 |
| 2 | 2026-03-15 (일) | 0 | 0경기 | 144경기 | PRESEASON | 작년 최종 |
| 3 | 2026-03-28 (토) | 5 (개막) | 1경기 | 144경기 | REGULAR_SEASON | GAMES |
| 4 | 2026-04-29 (수, today) | 5 | 25경기 | 144경기 | REGULAR_SEASON | GAMES |
| 5 | 2026-04-27 (월, 휴식) | 0 | 25경기 | 144경기 | REGULAR_SEASON | 진행 순위 |
| 6 | 2026-07-13 (월, 올스타) | 0 | 80경기 | 144경기 | REGULAR_SEASON | 진행 순위 |
| 7 | 2026-10-15 (목, 정규시즌 끝) | 1~3 (PO) | 144경기 | 144경기 | POSTSEASON | 5팀 + 오늘 경기 |
| 8 | 2026-10-30 (금, KS 진행) | 1 (KS) | 144경기 | 144경기 | POSTSEASON | 5팀 + 오늘 |
| 9 | 2026-11-20 (금, 시즌 종료) | 0 | 144경기 | 144경기 | OFFSEASON_AFTER | 올해 최종 |
| 10 | 2026-12-25 (금) | 0 | 144경기 | 144경기 | OFFSEASON_AFTER | 올해 최종 |
| 11 | 2027-01-15 (예: 미래) | 0 | (미래 → 400) | 144경기 | OFFSEASON_BEFORE | 작년 최종 |

**시뮬레이터 통과 기준**: 11/11 모두 기대 단계와 일치.

---

## 7. 회귀 방지

- 시뮬레이터를 GH Actions에서 매 PR마다 실행 (별도 workflow 신규 추가)
- `RealFetcher` 호출 횟수 모니터링 — 무한 fallback 방지
- 모든 standings API 호출에 timeout=10s + 재시도 0회 (워크플로우는 timeout 5분이라 짧게)

---

## 8. 구현 작업 분해

| Phase | 작업 | 예상 시간 |
|-------|------|----------|
| P1 | `SeasonStage` enum + `detect_season_stage` + `SeasonFetcher` protocol | 30분 |
| P2 | 시뮬레이터 `tools/simulate_branches.py` + 11개 케이스 픽스처 | 30분 |
| P3 | render 함수 3개 (offseason_before, offseason_after, postseason) | 30분 |
| P4 | `cmd_update` 분기 통합 | 15분 |
| P5 | 시뮬레이터로 회귀 테스트 + 실제 GH Actions 실행 검증 | 15분 |

총 약 2시간.

---

## 9. 미해결/추가 검토 필요

- **포스트시즌 진입 시점 정확 판정**: 현재 알고리즘은 "1팀이 144경기 마침"으로 판정. 하지만 일부 팀이 우천연기로 144 아래일 수 있음. → max(games) ≥ 144 OR sum(games) ≥ 720 같은 OR 조건 검토.
- **한국시리즈 종료 정확 판정**: 현재 11/15 캘린더 fallback. 더 정확하려면 PO 일정 API 별도 호출 필요. 일단 단순 fallback으로 시작 후 실제 가을 시즌에 데이터 보고 정교화.
- **시범경기 vs 정규시즌 일정 구분**: 일정 API의 `gameId`에 `kbo` 외 prefix가 있는지 추가 probe 필요. 일단은 "3월 22일 이전 = PRESEASON" 같은 캘린더 fallback.

---

## 10. 승인 요청

이 Plan대로 진행해도 될까요?
- (a) 그대로 진행
- (b) 수정 후 진행
- (c) 시뮬레이터 결과만 먼저 보고 결정

대기 중.
