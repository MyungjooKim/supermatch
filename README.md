# Supermatch

매일 KBO 경기 일정과 응원팀(LG/삼성/롯데) 결과 요약을 Slack Canvas에 자동 업데이트합니다.
**5단계 시즌 분기**로 1년 365일 의미있는 화면을 보장합니다 — 시즌 중·휴식일·포스트시즌·비시즌 전부.

## 동작 방식

```
GitHub Actions (매일 KST 08:00 / 17:00 / 20:00 / 23:30)
    ↓
[1] 시즌 단계 판정 (season_stage.detect_season_stage)
    standings API의 max(games)로 5단계 분류
[2] 단계별 데이터 fetch
    - 정규시즌: 일정 + (해당하면) 선발투수 + 박스스코어 + Claude 요약
    - 휴식일/포스트시즌/비시즌: 팀 순위
[3] 마크다운 청크 렌더링 (헤더 / 응원팀 카드 / 일정표 / 푸터)
[4] Slack Canvas 갱신
    - wipe → insert(청크별) → rename 순서로 호출
    - wipe는 5 pass retry, anchor 35+개로 phantom 섹션도 정리
```

같은 Canvas를 계속 갱신하므로 URL이 유지되고 채널 탭에 고정해두면 편합니다.

### 업데이트 주기 (KST)

| 시각 | 의도 |
|------|------|
| 08:00 | 새 하루 시작 — 어제 결과는 잠시 더 보이고 오늘 일정으로 전환 |
| 17:00 | 주중 18:30 / 주말 17:00 경기 시작 직전 — 선발투수 거의 확정 |
| 20:00 | 모든 경기 진행 중 — 라이브 점수 + 진행 상태 |
| 23:30 | 거의 모든 경기 종료 — 최종 결과 + 응원팀 1~2줄 요약 |

## 시즌별 화면

| 단계 | 시기 | 화면 |
|------|------|------|
| `OFFSEASON_BEFORE` | 1월~3월 초 | 작년 최종 순위 |
| `PRESEASON` | 시범경기 기간 (3월 초~중순) | 작년 최종 + 정규 개막 안내 |
| `REGULAR_SEASON` (경기있음) | 정규시즌 진행일 | 헤더 / 응원팀 카드 / 일정표 / 푸터 |
| `REGULAR_SEASON` (경기없음) | 정규시즌 휴식일 (월요일·우천 등) | 진행 중 팀 순위 |
| `POSTSEASON` | 정규시즌 종료 ~ 한국시리즈 끝 | 진출 5팀 + 오늘 PO 경기 |
| `OFFSEASON_AFTER` | 한국시리즈 끝 ~ 12월 31일 | 올해 최종 순위 |

응원팀(LG/삼성/롯데)은 모든 화면에서 **굵게 + ⭐**로 강조됩니다.

## 폴더 구조

```
supermatch/
├── .github/workflows/
│   ├── update-canvas.yml          # 매일 KST 08:00, 23:30 자동 실행
│   └── simulate-branches.yml      # season_stage 변경 시 시뮬레이터 자동 실행 (CI)
├── docs/
│   ├── 01-plan/
│   │   └── supermatch-season-states.md  # 시즌 분기 Plan
│   └── 03-report/
│       └── 2026-04-29-...md       # 작업 보고서
├── src/
│   ├── main.py                    # 엔트리포인트 (init / update / build_canvas_markdown)
│   ├── naver_kbo.py               # Naver API: fetch_schedule, fetch_box_score, fetch_team_stats
│   ├── render.py                  # 마크다운 렌더링 (단계별 화면 7종)
│   ├── slack_canvas.py            # Slack Canvas API (rename / list / delete / insert)
│   ├── season_stage.py            # 5단계 시즌 판정
│   └── summarize.py               # Claude API 요약 (haiku-4-5)
├── tools/
│   └── simulate_branches.py       # 시즌 분기 시뮬레이터 (11 케이스)
├── README.md
└── requirements.txt
```

## 셋업 (한 번만)

### 1. Slack 앱 만들기

1. https://api.slack.com/apps → **Create New App** → From scratch
2. **OAuth & Permissions** → Bot Token Scopes에 추가:
   - `canvases:write`
   - `canvases:read`
   - `channels:read` (Canvas를 채널 탭으로 붙일 경우)
3. 워크스페이스에 설치하고 **Bot User OAuth Token** (`xoxb-...`)을 복사
4. Canvas를 붙일 채널이 있다면 그 채널에 봇을 초대 (`/invite @your-bot`)

### 2. GitHub 레포 Secrets 등록

Settings → Secrets and variables → Actions:

| Name | Value |
|---|---|
| `SLACK_BOT_TOKEN` | 위에서 받은 `xoxb-...` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com 에서 발급 |
| `SLACK_CANVAS_ID` | 다음 단계에서 채워넣음 |

### 3. 최초 Canvas 생성 (로컬 1회)

```bash
git clone <your-repo>
cd supermatch
pip install -r requirements.txt

export SLACK_BOT_TOKEN=xoxb-...
export ANTHROPIC_API_KEY=sk-ant-...

# 채널 ID는 Slack에서 채널 → 채널 정보 맨 아래에서 복사
python src/main.py init --channel C0123456789
# → CANVAS_ID=F0XXXXXXX 이 값을 GitHub Secrets의 SLACK_CANVAS_ID에 저장
```

### 4. 끝

GitHub Actions이 매일 자동 실행됩니다. 수동 실행:

```bash
gh workflow run "Update Supermatch Canvas" --ref main
```

또는 Actions 탭 → "Update Supermatch Canvas" → Run workflow.

## 운영

### 로그 확인

```bash
gh run list --limit 5
gh run view <RUN_ID> --log | grep -E "stage|cleared|refreshed"
```

기대 출력 (정규시즌 평일):
```
[stage] 2026-04-29 → regular_season (games today: 5)
✓ title set: 오늘의 KBO :baseball:
✓ pass 1: attempted to clear 36 sections
✓ canvas confirmed empty after pass 1
✓ canvas refreshed
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
- **Canvas 디자인**: [src/render.py](src/render.py)의 각 `render_*` 함수
- **실행 시각**: [.github/workflows/update-canvas.yml](.github/workflows/update-canvas.yml)의 cron (UTC 기준)
- **시즌 단계 판정 기준**: [src/season_stage.py](src/season_stage.py)의 `detect_season_stage`

## 알아둘 것 / 한계

- **Naver API는 비공식**입니다. 스펙이 바뀌면 `naver_kbo.py`의 응답 파싱을 손봐야 합니다.
- **Phantom (빈) 표 잔존**: Slack Canvas의 `insert_at_end`가 마크다운 표 처리 시 빈 placeholder 표를 부수효과로 가끔 생성하는 quirk가 있습니다.
  - 우리가 *생성을 막지는* 못하지만, 다음 wipe 사이클에서 자동 정리됩니다 (anchor 정리 덕분)
  - 사용자 화면에는 잠시 보였다가 다음 cron에서 사라짐. 급하면 Slack에서 해당 표를 우클릭 → 삭제
  - 새 화면/필드 추가 시엔 **반드시 [src/main.py](src/main.py)의 `text_anchors`에 새 짧은 단어 추가** (자세한 건 docs/03-report 참조)
- **시즌 단계 판정의 fallback 한계**:
  - PO 진입은 `max(games) >= 144`로 판정 — 우천연기로 1팀만 144 미만이면 오판 가능
  - KS 종료는 `11/15` 캘린더 fallback (정확한 종료일은 PO 일정 API 별도 확인 필요)
  - 시범경기 vs 정규시즌 구분은 `3/22` 캘린더 fallback
  - 모두 워크플로우는 죽지 않음 — 실데이터 보고 점진적 정교화 예정
- **요약 비용**: 23:30 실행에서만 LG/삼성/롯데 × 1회 = 일 3회. Haiku 4.5 기준 한 달 1달러 미만.
- **GitHub Actions**: 4 × 30 = 120분/월, 무료 한도(2000분/월)의 6%만 사용.
- **데이터 정확도**: 박스스코어가 늦게 갱신되는 경기가 있어, 밤 11:30 실행으로 보완합니다.

## 작업 히스토리

| 날짜 | 보고서 | 핵심 |
|------|--------|------|
| 2026-04-29 (오전) | [docs/03-report/2026-04-29-canvas-bugfix-and-season-stages.md](docs/03-report/2026-04-29-canvas-bugfix-and-season-stages.md) | Canvas 누적 버그 수정 + 5단계 시즌 분기 + 시뮬레이터 |
| 2026-04-29 (오후) | [docs/03-report/2026-04-29-phantom-tables-and-4x-cron.md](docs/03-report/2026-04-29-phantom-tables-and-4x-cron.md) | Phantom 표 진짜 원인 (`"예정"` anchor 누락) + UX 마이크로 (이모지/타이틀/선발투수) + 4x/day cron |
