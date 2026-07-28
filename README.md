# Supermatch

매일 KBO 경기 일정과 응원팀(LG/삼성/롯데) 결과 요약을 웹 페이지로 자동 갱신합니다.
**5단계 시즌 분기**로 1년 365일 의미있는 화면을 보장합니다 — 시즌 중·휴식일·포스트시즌·비시즌 전부.

**보는 곳**: https://myungjookim.github.io/supermatch/

## 동작 방식

```
GitHub Actions (매일 KST 08:07 / 17:13 / 20:17 / 23:37)
    ↓
[1] 시즌 단계 판정 (season_stage.detect_season_stage)
    standings API의 max(games)로 5단계 분류
[2] 단계별 데이터 fetch
    - 정규시즌: 일정 + (해당하면) 선발투수 + 박스스코어 + Claude 요약
    - 휴식일/포스트시즌/비시즌: 팀 순위
[3] 마크다운 렌더링 (render.py)
[4] HTML 변환 → docs/index.html (page.py)
[5] docs/index.html + state 를 커밋·푸시
    ↓
GitHub Pages 가 자동 재배포 (source: main /docs)
```

### 업데이트 주기 (KST)

| 시각 | 의도 |
|------|------|
| 08:07 | 새 하루 시작 — 어제 결과는 잠시 더 보이고 오늘 일정으로 전환 |
| 17:13 | 주중 18:30 / 주말 17:00 경기 시작 직전 — 선발투수 거의 확정 |
| 20:17 | 모든 경기 진행 중 — 라이브 점수 + 진행 상태 |
| 23:37 | 거의 모든 경기 종료 — 최종 결과 + 응원팀 1~2줄 요약 (state 저장) |

어제 경기 요약은 23:37 실행에서만 Claude로 생성해 `state/yesterday_summary.json`에
저장하고, 나머지 3회 실행은 그 파일을 읽습니다 (박스스코어 API가 시간이 지나면
비어버리기 때문 + 요약 비용 절감).

## 시즌별 화면

| 단계 | 시기 | 화면 |
|------|------|------|
| `OFFSEASON_BEFORE` | 1월~3월 초 | 작년 최종 순위 |
| `PRESEASON` | 시범경기 기간 (3월 초~중순) | 작년 최종 + 정규 개막 안내 |
| `REGULAR_SEASON` (경기있음) | 정규시즌 진행일 | 헤더 / 응원팀 카드 / 일정 / 푸터 |
| `REGULAR_SEASON` (경기없음) | 정규시즌 휴식일 (월요일·우천 등) | 진행 중 팀 순위 |
| `POSTSEASON` | 정규시즌 종료 ~ 한국시리즈 끝 | 진출 5팀 + 오늘 PO 경기 |
| `OFFSEASON_AFTER` | 한국시리즈 끝 ~ 12월 31일 | 올해 최종 순위 |

응원팀(LG/삼성/롯데)은 모든 화면에서 **굵게 + ⭐**로 강조됩니다.

## 폴더 구조

```
supermatch/
├── .github/workflows/
│   ├── update-canvas.yml          # 매일 4회 자동 실행 (파일명은 히스토리상 유지)
│   └── simulate-branches.yml      # season_stage 변경 시 시뮬레이터 자동 실행 (CI)
├── docs/                          # ← GitHub Pages 소스
│   ├── index.html                 # 매 실행마다 자동 갱신 (직접 편집 금지)
│   ├── 01-plan/
│   │   └── supermatch-season-states.md
│   └── 03-report/
├── src/
│   ├── main.py                    # 엔트리포인트 (update)
│   ├── naver_kbo.py               # Naver API: fetch_schedule / box_score / team_stats
│   ├── render.py                  # 마크다운 렌더링 (화면 7종)
│   ├── page.py                    # 마크다운 → 정적 HTML
│   ├── season_stage.py            # 5단계 시즌 판정
│   └── summarize.py               # Claude API 요약 (haiku-4-5)
├── state/
│   └── yesterday_summary.json     # 어제 요약 캐시 (자동 갱신)
├── tools/
│   └── simulate_branches.py       # 시즌 분기 시뮬레이터 (11 케이스)
├── README.md
└── requirements.txt
```

## 셋업

### GitHub 레포 Secrets

Settings → Secrets and variables → Actions:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com 에서 발급 |

`GITHUB_TOKEN`은 Actions가 자동 제공하므로 등록 불필요합니다.

### GitHub Pages

Settings → Pages → Source: **Deploy from a branch** / Branch: `main` / Folder: `/docs`

> 공개 레포의 Pages는 URL을 아는 누구나 볼 수 있습니다. `noindex` 메타 태그로
> 검색엔진 노출만 막아둔 상태입니다.

## 로컬 실행

```bash
pip install -r requirements.txt

# 요약 없이 생성 (API 키 불필요) — 레이아웃 확인용
python src/main.py update --no-summaries

# 요약 포함
export ANTHROPIC_API_KEY=sk-ant-...
python src/main.py update
```

`docs/index.html`을 브라우저로 열어 확인합니다.

## 운영

### 수동 실행

```bash
gh workflow run "Update Supermatch Page" --ref main
```

또는 Actions 탭 → "Update Supermatch Page" → Run workflow.
`is_final_run: true`를 주면 23:37 실행과 동일하게 어제 요약을 새로 생성합니다.

### 로그 확인

```bash
gh run list --limit 5
gh run view <RUN_ID> --log | grep -E "stage|wrote|warn"
```

기대 출력 (정규시즌 평일):
```
[stage] 2026-07-28 → regular_season (games today: 5)
[yesterday] is_final_run=False
✓ loaded yesterday summary from state file (2026-07-27)
✓ wrote GitHub Pages HTML: docs/index.html
```

### 시즌 분기 시뮬레이터

판정 로직 변경 시 회귀 방지:

```bash
PYTHONPATH=src python3 tools/simulate_branches.py
# 11/11 통과해야 함
```

PR로 `src/season_stage.py`나 `tools/simulate_branches.py` 변경하면
`Simulate Season Branches` 워크플로우가 자동 실행됩니다.

## 커스터마이징

- **응원팀 변경**: [src/naver_kbo.py](src/naver_kbo.py)의 `TARGET_TEAMS`
- **요약 톤**: [src/summarize.py](src/summarize.py)의 `SYSTEM` 프롬프트
- **화면 레이아웃**: [src/render.py](src/render.py)의 각 `render_*` 함수
- **페이지 디자인 (CSS)**: [src/page.py](src/page.py)의 `_PAGE_TEMPLATE`
- **실행 시각**: [.github/workflows/update-canvas.yml](.github/workflows/update-canvas.yml)의 cron (UTC 기준)
- **시즌 단계 판정 기준**: [src/season_stage.py](src/season_stage.py)의 `detect_season_stage`

## 알아둘 것 / 한계

- **이모지는 `:shortcode:` 로 씁니다.** [src/page.py](src/page.py)의
  `SHORTCODE_TO_UNICODE`가 유니코드로 치환하며, **매핑에 없는 shortcode는
  출력에서 제거됩니다.** `render.py`에 새 이모지를 추가하면 이 매핑에도 추가하세요.
- **Naver API는 비공식**입니다. 스펙이 바뀌면 `naver_kbo.py`의 응답 파싱을 손봐야 합니다.
- **`docs/index.html`은 직접 편집하지 마세요** — 매 실행마다 덮어써집니다.
- **cron 자동 비활성화**: 공개 레포는 60일간 활동이 없으면 GitHub이 스케줄을
  자동 비활성화합니다. 매 실행이 커밋을 남기므로 실질적으로는 발생하지 않습니다.
- **시즌 단계 판정의 fallback 한계**:
  - PO 진입은 `max(games) >= 144`로 판정 — 우천연기로 1팀만 144 미만이면 오판 가능
  - KS 종료는 `11/15` 캘린더 fallback (정확한 종료일은 PO 일정 API 별도 확인 필요)
  - 시범경기 vs 정규시즌 구분은 `3/22` 캘린더 fallback
  - 모두 워크플로우는 죽지 않음 — 실데이터 보고 점진적 정교화 예정
- **요약 비용**: 23:37 실행에서만 LG/삼성/롯데 × 1회 = 일 3회. Haiku 4.5 기준 한 달 1달러 미만.
- **GitHub Actions**: 4 × 30 = 120분/월, 무료 한도의 6%만 사용.

## 작업 히스토리

| 날짜 | 보고서 | 핵심 |
|------|--------|------|
| 2026-04-29 (오전) | [docs/03-report/2026-04-29-canvas-bugfix-and-season-stages.md](docs/03-report/2026-04-29-canvas-bugfix-and-season-stages.md) | Canvas 누적 버그 수정 + 5단계 시즌 분기 + 시뮬레이터 |
| 2026-04-29 (오후) | [docs/03-report/2026-04-29-phantom-tables-and-4x-cron.md](docs/03-report/2026-04-29-phantom-tables-and-4x-cron.md) | Phantom 표 원인 + UX 마이크로 + 4x/day cron |
| 2026-06-05 | — | GitHub Pages 미러 추가 (`page.py`) |
| 2026-07-28 | — | **Slack Canvas 연동 제거 — GitHub Pages 단독 운영으로 전환** |
