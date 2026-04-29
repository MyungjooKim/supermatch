# 작업 보고서 — 2026-04-29

> 다음에 다시 작업할 때 빠르게 파악할 수 있도록 **무엇을 / 왜 / 어떻게** 했는지 정리.

## Executive Summary

| 항목 | 내용 |
|------|------|
| 작업일 | 2026-04-29 (수) |
| 시작 상태 | Canvas 본문이 매 실행마다 누적, title 미설정, 비시즌·미래 시즌에서 워크플로우 죽을 위험 |
| 완료 상태 | wipe-and-refill로 1세트만 유지, title 자동 설정, 5단계 시즌 분기로 365일 안정 동작 |
| 커밋 수 | 11개 (`02b5f95` ~ `4264488`) |
| 신규 파일 | `src/season_stage.py`, `tools/simulate_branches.py`, `.github/workflows/simulate-branches.yml`, `docs/01-plan/supermatch-season-states.md` |
| Plan 매칭률 | 11/11 시뮬레이터 케이스 통과 (100%) + 실제 GH Actions run 정상 |

| Perspective | 내용 |
|-------------|------|
| Problem | 누적 버그 + 비시즌 미지원 + 모델 ID/시간대 등 잔버그 |
| Solution | (1) wipe-and-refill 전환 (2) Slack API 제약 우회 (3) 5단계 시즌 분기 + 시뮬레이터 |
| Function UX | 1세트만 깨끗하게, title 자동 갱신, 모든 날짜에 의미있는 화면 |
| Core Value | 365일 깨지지 않는 KBO 데일리 브리핑. 운영자 매일 확인 불필요 |

---

## 1. 작업한 두 가지 큰 줄기

### 줄기 A: Canvas 누적 버그 수정 (커밋 `02b5f95` ~ `38df1c7`)

**문제**: 매 실행마다 Canvas에 새 섹션이 추가만 되고 이전 데이터가 안 지워짐. 스크린샷에 헤더 4개·팀카드 3세트 누적.

**근본 원인**: 기존 코드는 anchor 텍스트로 섹션 lookup → replace 방식. Slack `contains_text`가 부분 매칭이라 여러 섹션을 동시에 잡으면서 `sections[0]`만 교체하고 나머지는 살아남았음.

**해결 (wipe-and-refill)**: 매 실행마다 Canvas의 모든 섹션을 lookup해서 다 삭제 → 새 본문 한 번에 삽입.

**그 과정에서 학습한 Slack API 제약** (`src/slack_canvas.py` 주석에 박제):
- `canvases.sections.lookup`의 `section_types` enum: `h1`/`h2`/`h3`/`any_header`만 (max 3개), `any_text` 없음
- `contains_text`: 빈 문자열 거부 (must be > 0 chars)
- `canvases.edit`: 한 호출에 `changes` 배열 1개만 허용 (배치 삭제 불가, 순차 호출 필요)
- → "전체 섹션 한 번에 받기" 깔끔한 API 없음 → `any_header` + 본문에 자주 등장하는 단어들로 다중 lookup 후 합집합 삭제, 5 pass retry로 cascade 잔여까지 정리

**관련 보너스 수정**:
- 푸터 시간 UTC → KST (`render.py:159`)
- 모델 ID `claude-opus-4-5` → `claude-haiku-4-5` (1~2줄 요약엔 충분, 5x 저렴)
- Title 매 실행 갱신 — `canvases.edit` `rename` operation 사용

### 줄기 B: 5단계 시즌 분기 + 시뮬레이터 (커밋 `a34e8cf` ~ `4264488`)

**문제**: 기존은 "오늘 경기 있음/없음" 2분기. 비시즌(1~3월)에 standings API가 빈 응답 또는 미래 시즌은 400으로 워크플로우 죽음. 포스트시즌·시즌 종료 후엔 의미 다른 데이터를 같은 화면으로 보여줌.

**해결**: 5단계 시즌 분기.

```
OFFSEASON_BEFORE  (1월~시즌 시작 전)  → 작년 최종 순위
PRESEASON         (시범경기 기간)      → 작년 최종 + 정규 개막 안내
REGULAR_SEASON    (정규시즌 진행 중)
  ├ 경기 있음 → GAMES 화면 (헤더/응원팀 카드/일정표/푸터)
  └ 경기 없음 → 진행 중 순위표
POSTSEASON        (정규시즌 종료~KS 끝) → 진출 5팀 + 오늘 PO 경기
OFFSEASON_AFTER   (KS 끝~12월 31일)  → 올해 최종 순위
```

**판정 알고리즘** ([src/season_stage.py](../../src/season_stage.py)):
```python
max_games = fetcher.this_year_max_games(year)  # standings API 호출

if max_games in (None, 0):
    # 미래 시즌(400) or 시즌 시작 전 (응답은 200이지만 모든 팀 0경기)
    if today.month == 3 and today.day < 22: return PRESEASON
    return OFFSEASON_BEFORE

if max_games >= 144:
    # 1팀이라도 144경기 마침 → 정규시즌 종료
    if today.month >= 12 or (today.month == 11 and today.day >= 15):
        return OFFSEASON_AFTER
    return POSTSEASON

return REGULAR_SEASON
```

**의존성 역전 (DI)**: `SeasonFetcher` Protocol을 받아서 시뮬레이터에서 mock 주입 가능. `RealFetcher`는 실제 Naver API 호출.

**시뮬레이터** ([tools/simulate_branches.py](../../tools/simulate_branches.py)):
- 11개 픽스처 케이스 (1월/3월 시범/3월말 개막/4월/월요일/올스타/PO 시작/KS/시즌 종료/12월/2027 미래)
- 종료 코드로 CI 통합 (`simulate-branches.yml` 워크플로우가 PR마다 실행)

**렌더 함수 신규 4개** ([src/render.py](../../src/render.py)):
- `render_offseason_before` — 1월 비시즌
- `render_preseason` — 시범경기 기간
- `render_offseason_after` — 시즌 종료 후
- `render_postseason_top5` — 포스트시즌

응원팀(LG/삼성/롯데) 강조: 굵게 + ⭐. 최근 5경기는 컬러 점(🟢🔴⚪).

---

## 2. 코드 구조 (현재 상태)

```
supermatch/
├── .github/workflows/
│   ├── update-canvas.yml          # 매일 KST 08:00, 23:30 자동 실행
│   └── simulate-branches.yml      # season_stage.py 변경 시 시뮬레이터 자동 실행
├── docs/
│   ├── 01-plan/
│   │   └── supermatch-season-states.md   # 시즌 분기 Plan 문서
│   └── 03-report/
│       └── 2026-04-29-...md       # 이 문서
├── src/
│   ├── main.py                    # cmd_init / cmd_update / build_canvas_markdown (5단계 분기)
│   ├── naver_kbo.py               # Naver API 래퍼: fetch_schedule, fetch_box_score, fetch_team_stats
│   ├── render.py                  # 마크다운 렌더링 (GAMES/standings/offseason 4종)
│   ├── slack_canvas.py            # Slack Canvas API 래퍼 (rename / list_all_sections / delete / insert)
│   ├── season_stage.py            # 5단계 enum + detect_season_stage + RealFetcher
│   └── summarize.py               # Claude API 요약 (haiku-4-5)
├── tools/
│   └── simulate_branches.py       # 시즌 분기 시뮬레이터 (11 케이스)
├── README.md
└── requirements.txt
```

### 데이터 흐름

```
GitHub Actions (cron)
  ↓
src/main.py update
  ↓
  detect_season_stage(today, RealFetcher())   # season_stage.py
  ↓
  build_canvas_markdown(today)                  # main.py
    ├ stage == OFFSEASON_BEFORE/PRESEASON → fetch_team_stats(year-1) → render_offseason_before/preseason
    ├ stage == REGULAR_SEASON + 경기있음 → fetch_schedule + summarize → render_full_canvas
    ├ stage == REGULAR_SEASON + 경기없음 → fetch_team_stats(year) → render_full_standings
    ├ stage == POSTSEASON → fetch_team_stats(year)[:5] → render_postseason_top5
    └ stage == OFFSEASON_AFTER → fetch_team_stats(year) → render_offseason_after
  ↓
  Slack Canvas API (slack_canvas.py)
    ├ rename (title 매 실행 갱신)
    ├ list_all_sections + delete (5 pass retry로 잔여 정리)
    └ insert_at_end (새 본문 1세트)
```

---

## 3. 운영 가이드

### 매일 자동 실행
- KST 08:00 (전날 결과 정리 시점)
- KST 23:30 (당일 경기 종료 후)
- GitHub Actions: https://github.com/MyungjooKim/supermatch/actions

### 수동 실행
```bash
gh workflow run "Update Supermatch Canvas" --ref main
gh run watch <RUN_ID>
```

### 로그 확인
```bash
gh run view <RUN_ID> --log | grep -E "stage|cleared|refreshed"
```

기대 출력 (예: 정규시즌 평일):
```
[stage] 2026-04-29 → regular_season (games today: 5)
✓ title set: 오늘의 KBO :baseball:
✓ pass 1: attempted to clear 36 sections
✓ canvas confirmed empty after pass 1
✓ canvas refreshed
```

### 시즌 분기 시뮬레이터 실행
```bash
PYTHONPATH=src python3 tools/simulate_branches.py
# 11/11 통과해야 함
```

### Secrets (GitHub repo settings → Secrets and variables → Actions)
- `SLACK_BOT_TOKEN` — `xoxb-...` (Slack 앱 OAuth)
- `SLACK_CANVAS_ID` — `F...` (Canvas 식별자)
- `ANTHROPIC_API_KEY` — `sk-ant-...`

### 의존성
- Python 3.12
- `anthropic >= 0.40.0`
- `requests >= 2.31.0`

---

## 4. 알려진 미해결 항목 (다음에 작업할 때)

Plan 9장 ([supermatch-season-states.md](../01-plan/supermatch-season-states.md))에서 **"가을 시즌 실데이터 보고 정교화"**로 넘긴 항목들:

| ID | 내용 | 현재 상태 | 정교화 시점 |
|----|------|---------|-----------|
| M1 | PO 진입 시점 정확 판정 | `max(games) >= 144`로 판정. 우천연기로 1팀만 144 미만일 때 오판 가능 | 2026-10월에 실데이터 확인 |
| M2 | KS 종료 정확 판정 | `11/15` 캘린더 fallback. PO 일정 API 별도 호출이 더 정확 | 2026-11월 |
| M3 | 시범경기 vs 정규시즌 일정 구분 | `gameType` 필드 응답에 없음. 일정 `gameId`/날짜로 추정 | 2027-3월 |
| M4 | Node.js 20 deprecated 경고 | GH Actions runner가 자동으로 24로 옮겨갈 예정 (2026-06-02) | 그 후 |

이 4가지는 **워크플로우를 죽이지 않는** 항목들 — 기본 fallback이 있어 의미있는 화면은 항상 표시됨. 정교화는 실제 가을 시즌 데이터 보고 케이스 추가하면 됨.

---

## 5. 다음 작업자가 알아야 할 것 (FAQ)

### Q. Canvas가 다시 누적되면?
**원인 후보**:
- 새 본문에 등장하는 단어가 [main.py text_anchors](../../src/main.py)에 없음 → 잔여 섹션 lookup 못함
- Slack API가 새 enum을 추가하거나 제약을 변경

**대응**:
1. `gh run view <RUN_ID> --log | grep "pass"` 로 retry pass 동작 확인
2. `pass N: attempted to clear K`에서 K=0인데 화면에 누적이 보이면 anchor 누락
3. text_anchors에 새 단어 추가 후 push

### Q. Slack API 호출이 401/403으로 실패하면?
- `SLACK_BOT_TOKEN` 만료 — Slack 앱 페이지에서 재발급
- Canvas 권한 부족 — `canvases:write`, `canvases:read`, `channels:read` 스코프 확인

### Q. 시즌 단계 판정이 의도와 다르게 동작하면?
1. 시뮬레이터에 새 케이스 추가 (`tools/simulate_branches.py` `CASES` 리스트)
2. `detect_season_stage` 로직 수정
3. 시뮬레이터 11+1 케이스 모두 통과 확인 (`PYTHONPATH=src python3 tools/simulate_branches.py`)
4. PR 올리면 `Simulate Season Branches` 워크플로우가 자동 검증

### Q. 새 화면 추가하려면?
1. `src/render.py`에 `render_*` 함수 추가
2. `src/main.py` `build_canvas_markdown`에 분기 추가
3. (필요 시) `season_stage.py`에 새 단계 enum 추가
4. 시뮬레이터 케이스 추가
5. `text_anchors`에 새 화면에 등장하는 단어 추가 (누적 방지)

---

## 6. 커밋 히스토리 한눈에 보기

| 커밋 | 작업 |
|------|------|
| `02b5f95` | wipe-and-refill 전환 (anchor 패턴 폐기) |
| `c2c8583` | section_types 5→2 (Slack API max 3) |
| `0e0e085` | contains_text 빈 문자열 거부 → criteria 변경 |
| `dc9d39f` | any_header + 텍스트 anchor 다중 매칭으로 변경 |
| `5d70d83` | Canvas title `rename` operation 추가 |
| `87cb127` | delete를 한 번에 1개씩 (changes 배열 max 1) |
| `38df1c7` | 5 pass retry + anchor 30+개 확장 |
| `a34e8cf` | Step A: 경기 없는 날 standings 표시 |
| `3a86c52` | Step B: 5단계 시즌 분기 + 시뮬레이터 11/11 |
| `4264488` | 시뮬레이터 자동 실행 CI 워크플로우 |

상세 diff는 `git show <commit>` 또는 GitHub PR 페이지 참고.
