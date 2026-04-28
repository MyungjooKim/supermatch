# Supermatch

매일 KBO 경기 일정과 응원팀(LG/삼성/롯데) 결과 요약을 Slack Canvas에 자동 업데이트합니다.

## 동작 방식

```
GitHub Actions (매일 08:00, 23:30 KST)
    ↓
[1] Naver 스포츠 비공식 API에서 오늘 경기 + 박스스코어 가져오기
[2] LG/삼성/롯데 박스스코어를 Claude API로 1~2줄 요약
[3] Canvas markdown 렌더링 (헤더/팀카드/일정표/푸터 4개 섹션)
[4] Slack canvases.sections.lookup → canvases.edit (replace) 로 섹션별 갱신
```

같은 Canvas를 계속 갱신하므로 URL이 유지되고 채널 탭에 고정해두면 편합니다.

## 폴더 구조

```
kbo-canvas/
├── .github/workflows/update-canvas.yml   # 스케줄러
├── src/
│   ├── main.py            # 엔트리포인트 (init / update)
│   ├── naver_kbo.py       # Naver API 래퍼
│   ├── summarize.py       # Claude API 요약
│   ├── render.py          # 마크다운 렌더링
│   └── slack_canvas.py    # Slack Canvas API 래퍼
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

### 2. GitHub 레포 만들고 Secrets 등록

레포 Settings → Secrets and variables → Actions → New repository secret:

| Name | Value |
|---|---|
| `SLACK_BOT_TOKEN` | 위에서 받은 `xoxb-...` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com 에서 발급 |
| `SLACK_CANVAS_ID` | 다음 단계에서 채워넣음 |

### 3. 최초 Canvas 생성 (로컬 1회)

```bash
git clone <your-repo>
cd kbo-canvas
pip install -r requirements.txt

export SLACK_BOT_TOKEN=xoxb-...
export ANTHROPIC_API_KEY=sk-ant-...

# 채널 ID는 Slack에서 채널 → 채널 정보 맨 아래에서 복사
python src/main.py init --channel C0123456789
# → CANVAS_ID=F0XXXXXXX 이 값을 GitHub Secrets의 SLACK_CANVAS_ID에 저장
```

### 4. 끝

GitHub Actions이 매일 자동 실행됩니다. 수동 실행은 Actions 탭 → "Update KBO Canvas" → Run workflow.

## 커스터마이징

- **응원팀 변경**: `naver_kbo.py`의 `TARGET_TEAMS` 와 `render.py`의 `render_team_section()` order
- **요약 톤**: `summarize.py`의 `SYSTEM` 프롬프트
- **Canvas 디자인**: `render.py` 의 각 `render_*` 함수
- **실행 시각**: `.github/workflows/update-canvas.yml` 의 cron (UTC 기준)

## 알아둘 것 / 한계

- **Naver API는 비공식**입니다. 스펙이 바뀌면 `naver_kbo.py`의 응답 파싱을 손봐야 합니다. 망가졌을 때 빠르게 알아채려면 GitHub Actions 실패 알림을 켜두세요.
- **포스트시즌은 일정 구조가 다를 수 있습니다.** 현재 코드는 정규시즌 기준이며 포스트시즌엔 검증이 필요합니다.
- **요약 비용**: 하루 최대 6회 호출 (LG/삼성/롯데 × 아침/밤). Opus 기준 한 달 1~2달러 수준입니다.
- **데이터 정확도**: 박스스코어가 늦게 갱신되는 경기가 있어, 밤 11:30 실행으로 보완합니다.
