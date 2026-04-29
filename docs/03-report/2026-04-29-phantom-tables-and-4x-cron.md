# 작업 보고서 — 2026-04-29 (오후 세션)

> 같은 날 오전 세션의 후속편. 오전 보고서:
> [2026-04-29-canvas-bugfix-and-season-stages.md](2026-04-29-canvas-bugfix-and-season-stages.md)
>
> 운영 중 발견된 phantom 표 누적 문제, UX 마이크로 개선, 업데이트 주기 4x/day 확장을 정리.

## Executive Summary

| 항목 | 내용 |
|------|------|
| 작업일 | 2026-04-29 (수) 오후 |
| 시작 상태 | 오전 작업 후 운영 중 — phantom 빈 표가 매 실행마다 누적되는 증상 발견 |
| 완료 상태 | 진짜 원인 (`"예정"` anchor 누락) 발견 + 수정. UX 마이크로 개선 (이모지/타이틀/선발투수). 운영 주기 2x → 4x/day |
| 커밋 수 | 17개 (`424692d` ~ `d25fb7b`) |
| 운영 시작 | KST 17:25 — 다음 자동 실행 KST 20:00 |

| Perspective | 내용 |
|-------------|------|
| Problem | (1) 매 실행 후 빈 placeholder 표 1~5개가 본문 위에 쌓이는 증상 (2) 사용자가 양 팀 선발투수, 응원팀 강조, 단조로운 H1 등 UX 개선 요청 (3) 업데이트가 하루 2번이라 진행 중 점수 안 보임 |
| Solution | (1) 진단 코드로 wipe 후에도 살아있는 섹션 카운트 → `"예정"` 단독 anchor가 빠진 게 원인. 한 글자 추가로 해결 (2) Title 양쪽 야구공 / H1 `🧢 우리 팀 오늘` / LG 👬 / 선발투수 양 팀 표기 (3) cron 4회 (08/17/20/23:30 KST) |
| Function UX | 빈 표가 자동 정리되어 사용자 수동 청소 빈도↓. 응원팀 정보 풍부화 (선발투수 가시화). 저녁 8시 라이브 점수 + 진행 상태 새로 가시화 |
| Core Value | 운영 모드 안정 진입. 매 시간대마다 의미 있는 화면 + 자가 정리 |

---

## 1. Phantom 표 디버깅의 여정 (가장 가치 있는 학습)

### 발견된 증상
- 매 GH Actions run 후 Canvas 본문 위에 빈 placeholder 표가 1~5개 추가됨
- 본문 자체는 정상 (헤더 → 응원팀 카드 → 일정표 → 푸터)
- 사용자가 수동으로 비워도 다음 자동 실행 후 다시 누적

### 시간 순으로 검증한 가설들 (모두 ❌)

| 가설 | 시도 | 결과 |
|------|------|------|
| H1 `rename` 다음 wipe가 title을 함께 지운다 | 순서를 wipe → insert → rename으로 변경 (`424692d`) | title은 정상 회복했지만 phantom 표는 여전 |
| 마크다운 테이블이 phantom 표의 원인 | 일정표를 bullet list로 변환 (`26721f3`) | 효과 없음 → 다시 표로 복원 (`3134103`) |
| `criteria` 없이 lookup 가능 | `criteria: {}` / 키 생략 시도 (`b4d6f03` 진단) | Slack이 거부 — `must have minimum 1 properties` |
| Section type enum이 더 있을 수도 | `table/list/paragraph/text/any` 시도 (`e00427b` 진단) | 모두 거부 — `h1/h2/h3/any_header`만 유효 |
| `canvases.info/get/read/contents` 메서드로 본문 read | (`e00427b` 진단) | 모두 `unknown_method` |
| 큰 markdown 한 번에 보내서 phantom 생성 | 4개 청크로 분할 insert (`bd438c4`) | 효과 없음 |

### 진짜 원인 발견
청크별 진단(`6a9c804`)에서 `wipe → insert → 각 청크 후 lookup 카운트` 추적했더니:

```
[DIAG after-wipe] any_header: 0
[DIAG after-wipe] contains_예정: 10    ← wipe 후 "empty 확인" 보고에도 10개 살아있음!
```

**원인**: `text_anchors`에 `"경기 예정"`(공백 포함)만 있었고 단독 `"예정"`이 빠져있었음. 정상 일정표 status 셀과 phantom 표 status 셀 모두에 `"예정"`이 단독으로 들어있는데, wipe loop은 이걸 매칭 못 해서 "empty 확인됨"으로 거짓 보고.

### 수정 (한 줄)
[src/main.py text_anchors](../../src/main.py)에 `"예정",` 추가 (`4de1169`):
```python
text_anchors = [
    "vs",
    "구장",
    "데이터",
    "예정",          # ← 이 한 줄이 모든 phantom 누적의 진짜 원인
    "경기 예정",
    ...
]
```

### 검증 결과
같은 진단 코드로 재측정:
```
[DIAG after-wipe] contains_예정: 0     ← 정확히 0개 (이전 10)
[DIAG after-chunk-3] contains_예정: 8  ← 일정표 + 응원팀 카드의 정상 매칭만
```

### 교훈
- Slack `canvases.sections.lookup`은 `contains_text`로 *부분 일치*하지만, 우리가 anchor에 짧은 단어를 넣지 않으면 짧은 텍스트만 가진 섹션은 영원히 못 잡는다
- 디버깅 시간 최소 4시간을 절약할 수 있었던 한 가지 — **wipe 직후 lookup으로 진짜 0인지 검증하는 진단**을 더 일찍 했어야 함
- 새 화면/필드 추가할 때마다 `text_anchors`도 같이 업데이트하는 운영 규칙이 필요

### 받아들인 한계
Phantom 표는 Slack 측 quirk이라 우리가 *생성을 막을* 수는 없음. 하지만 **다음 wipe 사이클에서 자동 정리**되는 게 보장됨. 사용자 화면에는 잠깐(1 cron 주기) 보였다가 사라짐. 사용자가 매번 수동 정리할 필요는 없음.

---

## 2. UX 마이크로 개선

### Title (탭 + 본문 큰 글씨)
```
"오늘의 KBO :baseball:"  →  ":baseball: 오늘의 KBO :baseball:"
```
양쪽 대칭 ⚾.

### H1 헤더 (본문 첫 줄)
변경 시도 여러 번:
1. `:baseball:` 단독 → 평범 (시작 상태)
2. `:baseball::raised_back_of_hand:` (타석 느낌) — 사용자가 별로
3. `:cap:` (보통 모자) — 사용자가 야구 모자 명시 원함
4. **`:billed_cap:` 우리 팀 오늘** (최종)

추가로 `## :star: 우리 팀 오늘` 섹션 헤더 제거 (H1으로 통합).

### LG 트윈스 이모지
```
:baseball:  →  :two_men_holding_hands: (👬)
```
LG 트윈스 어원에 충실.

### 선발투수 표기 (양 팀)
[src/naver_kbo.py](../../src/naver_kbo.py) `fetch_starting_pitchers` 신규:
- `/schedule/games/{id}/preview` endpoint 사용 (probe 결과 발견)
- `gameInfo.hCode` 비교로 home/away 정규화 (preview API도 schedule API처럼 표시 우선순위 기준이라 reversedHomeAway 등가 처리 필요)

표시 형식:
```
👬 LG 트윈스(이정용)
vs KT 위즈(소형준) · 잠실 · 18:30 경기 예정
```

---

## 3. 운영 주기 확장 (2x → 4x/day)

이전: KST 08:00 / 23:30 (2회)
신규: KST **08:00 / 17:00 / 20:00 / 23:30** (4회)

| 시각 | 의도 |
|------|------|
| 08:00 | 새 하루 시작 — 어제 결과는 잠시 더 보이고 오늘 일정으로 전환 |
| 17:00 | 주중 18:30 / 주말 17:00 경기 시작 직전. 선발투수 거의 확정 |
| 20:00 | 모든 경기 진행 중. 라이브 점수 + 진행 상태 |
| 23:30 | 거의 모든 경기 종료. 최종 결과 + 응원팀 1~2줄 요약 |

### 비용
- Claude API (Haiku 4.5 요약): 23:30만 요약 호출 → 월 $1 미만
- GitHub Actions: 4 × 30 × 1분 = 120분/월, 무료 한도(2000분/월)의 6%

[.github/workflows/update-canvas.yml](../../.github/workflows/update-canvas.yml) 변경.

---

## 4. 코드 변경 요약 (오후 세션)

### `src/main.py`
- `build_canvas_chunks(date) -> list[str]` 신규 — REGULAR_SEASON+경기있음 케이스를 4 청크로 분할
- `build_canvas_markdown` 호환 유지 (cmd_init에서 사용)
- `cmd_update`: rename 호출을 wipe 다음으로 이동 (title이 wipe에 같이 지워지던 문제 해결)
- `text_anchors`에 `"예정"`, `"홈"`, `"우리 팀"`, `"KBO"` 추가 (phantom 정리 + H1 매칭)
- `fetch_starting_pitchers` import 추가

### `src/naver_kbo.py`
- `fetch_starting_pitchers(game_id, true_home_code) -> dict[str, str]` 신규
- preview API의 reversedHomeAway 정규화 로직 포함

### `src/render.py`
- `render_header`: H1 `:billed_cap: 우리 팀 오늘` (was `:baseball: 오늘의 KBO`)
- `render_team_card(team_code, game, summary, starters)` — `starters` 인자 추가
- `render_team_section`: `## :star: 우리 팀 오늘` 헤더 제거, `starters_by_game` 인자 추가
- `render_full_canvas`: `starters_by_game` 인자 전달
- `_name_with_starter` 헬퍼 신규
- `TEAM_EMOJI["LG"]`: `:baseball:` → `:two_men_holding_hands:`

### `.github/workflows/update-canvas.yml`
- cron 2개 → 4개

---

## 5. 코드 안 한 것 (의도적 deferral)

- **새 Canvas 생성으로 깨끗한 시작**: 사용자가 "같은 코드로 같은 결과"라며 합리적 거부. 동일 의견 동의함
- **마크다운 raw block 형식 시도**: 시간 부족 + Slack docs 빈약 + 효과 불확실
- **빈 표 생성 자체 막기**: Slack 측 quirk라 우리가 통제 못 함. 자동 정리 사이클로 받아들임

---

## 6. 운영 가이드

### 일상 운영
대부분 사용자 개입 불필요. GH Actions 4회 자동 실행이 모든 화면 갱신.

### Phantom 표가 본문 위에 1~5개 보일 때
- **다음 자동 실행 (최대 ~6시간)** 까지 기다리면 자동 정리
- 급하면 수동 트리거: `gh workflow run "Update Supermatch Canvas" --ref main`
- 그래도 안 사라지면 anchor에 새 키워드가 필요한 경우 — 보고서 9장 참조

### 새 화면/필드 추가할 때
1. `src/render.py`에서 새 텍스트 렌더링
2. **반드시 [src/main.py:text_anchors](../../src/main.py)에 새 화면의 짧은 단어 추가**
3. 시뮬레이터 통과 확인 (`PYTHONPATH=src python3 tools/simulate_branches.py`)

### 다음에 phantom 표가 또 누적되면
1. 진단 코드 잠깐 추가 (오늘 적용한 `_probe_count` 패턴):
   ```python
   for crit in [{"contains_text": w} for w in ["새 단어 후보 1", "후보 2"]]:
       data = slack._post("canvases.sections.lookup", {"canvas_id": ..., "criteria": crit})
       print(f"{w}: {len(data.get('sections') or [])}")
   ```
2. 어느 단어로 lookup하면 잔여 섹션이 잡히는지 확인
3. 그 단어를 `text_anchors`에 추가
4. 진단 제거, 운영

---

## 7. 다음에 다시 작업할 때 (FAQ 추가본)

오전 보고서의 FAQ에 이번 세션 추가:

### Q. 빈 표가 자꾸 생기는데 왜?
Slack `canvases.edit insert_at_end`의 비명시적 동작 — 마크다운 테이블 처리 시 placeholder 표를 부수효과로 생성. **우리 코드가 만드는 게 아님**. 다음 wipe 사이클에서 자동 정리되도록 anchor 관리만 잘 하면 됨.

### Q. wipe 후 "empty 확인됨" 메시지 신뢰해도 되나?
**아니오** — 우리 anchor에 매칭되는 섹션이 0이라는 의미일 뿐, **anchor에 안 잡히는 섹션은 살아있어도 "empty"로 보고**됨. 의심되면 진단 코드로 추가 검증.

### Q. 청크 분할 (chunk 1~4)이 무슨 효과?
**없음** — phantom 표 발생률을 줄이지 못했다. 단지 디버깅 가치(어느 청크에서 phantom 생기는지 추적)와 코드 가독성 향상만 있음. 그래도 코드는 이 방식 유지 (build_canvas_chunks 패턴이 더 명료).

### Q. 선발투수 정보가 안 뜨면?
- `/schedule/games/{id}/preview`의 `awayStarter.playerInfo.name`이 비어있을 수 있음 (선발 미발표)
- 표시는 fallback — 빈 문자열이면 팀명만 (괄호 없이)
- 8시 실행 때는 거의 비어있고, 17시/20시/23시 실행 때 채워짐

### Q. cron 시간을 또 바꾸려면?
[.github/workflows/update-canvas.yml](../../.github/workflows/update-canvas.yml) 의 cron 식. **GitHub Actions는 UTC 기준, KST에서 9시간 빼서 입력**.

---

## 8. 운영 모니터링 포인트

### 주간
- KST 23:30 run 결과의 응원팀 카드 (LG/SS/LT 결과 + 1~2줄 요약 적정성)
- 빈 표 잔존 — 1세트 정도면 정상 (다음 cron에서 정리), 5개 이상 누적되면 anchor 보강 필요

### 월간
- GitHub Actions 사용량 (Settings → Billing) — 무료 한도 6% 사용 중
- Anthropic API 사용량 — 월 $1 미만 유지
- Slack Bot 토큰 만료 — `xoxb-...` rotation 필요 시

### 가을 (10~11월)
- Plan 9장 미해결 항목들 (PO 진입/KS 종료/시범경기 구분 정교화)
- 시뮬레이터에 실제 가을 시즌 데이터 케이스 추가

---

## 9. 커밋 히스토리 (오후 세션만)

| 커밋 | 내용 |
|------|------|
| `424692d` | rename 호출 순서를 wipe 후로 이동 (title 보존) |
| `c9c83bb` | [DIAG] insert 후 섹션 dump |
| `b4d6f03` | [DIAG] contains_text + flush |
| `f28d050` | "홈" anchor 추가 + 위 진단 제거 |
| `d0b52ad` | [DIAG] criteria 없는 lookup probe |
| `26721f3` | 일정표 마크다운 → bullet 리스트 (실패) |
| `3134103` | 일정표 표 형식 복원 |
| `04cb1ca` | phase 2 진단 helper 제거 |
| `e00427b` | [DIAG] canvases.info/get + section_types enum probe |
| `3965d9e` | 선발투수 표기 + 이모지 (Title 양쪽 ⚾, LG 👬) |
| `000e948` | H1 `:cap:` 시도 |
| `2870d82` | H1 `:billed_cap: 우리 팀 오늘`, 섹션 헤더 제거 |
| `bd438c4` | 청크 분할 insert |
| `6a9c804` | [DIAG] 청크별 카운트 추적 |
| **`4de1169`** | **`"예정"` anchor 추가 — 진짜 원인 수정** |
| `1463f69` | 진단 제거 |
| `d25fb7b` | cron 4x/day |

진단 커밋 6개 (`[DIAG]`)가 길게 남았지만 의도적 — 다음에 비슷한 디버깅 필요할 때 git history에서 패턴 참조 가능.
