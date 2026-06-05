"""
Slack Canvas용으로 만든 동일한 마크다운을 GitHub Pages용 정적 HTML로 변환합니다.

설계 의도:
- Canvas 경로(main.py:cmd_update)와 **완전히 병행**. Slack 출력은 건드리지 않고,
  build_canvas_markdown()이 만든 같은 마크다운 문자열을 입력으로 받아 HTML만 생성.
- 의존성 최소화: markdown 라이브러리 1개만 추가 (requirements.txt).
- 단일 self-contained HTML (외부 CSS/JS 파일 없음). docs/index.html 하나로 배포.

Slack 전용 표기 보정:
- `:shortcode:` 이모지 → 유니코드 이모지로 대체. 워크스페이스 커스텀 전용이라
  대체 불가한 것은 제거 (웹에 raw `:xxx:` 노출 방지).
"""

from __future__ import annotations

import datetime as dt
import re

import markdown as _md

# naver_kbo 의 KST 를 재사용 (푸터 시각 표기에 사용)
try:
    from naver_kbo import KST
except Exception:  # pragma: no cover - import 경로 방어
    KST = dt.timezone(dt.timedelta(hours=9))


# ── Slack shortcode → 유니코드 이모지 매핑 ──────────────────────────────────
# render.py 에서 실제 사용 중인 shortcode 전체를 커버.
# 표준 이모지는 유니코드로, 팀 커스텀은 가장 가까운 동물/상징 이모지로 대체.
# 매핑에 없는 `:xxx:` 는 정규식으로 일괄 제거.
SHORTCODE_TO_UNICODE: dict[str, str] = {
    # 표준
    "baseball": "⚾",
    "coffee": "☕",
    "fire": "🔥",
    "star": "⭐",
    "trophy": "🏆",
    "bar_chart": "📊",
    "clipboard": "📋",
    "round_pushpin": "📍",
    "snowflake": "❄️",
    "zzz": "💤",
    "rewind": "⏪",
    "wrench": "🔧",
    # KBO 팀 (render.py TEAM_EMOJI 와 1:1 대응)
    "tiger": "🐯",        # HT 기아
    "eagle": "🦅",        # WO 키움
    "bear": "🐻",         # OB 두산
    "ship": "🚢",         # SK SSG
    "t-rex": "🦖",        # NC
    "magic_wand": "🪄",   # KT
    # 워크스페이스 커스텀 팀 이모지 → 가장 가까운 유니코드
    "lg_lucky": "🟥",     # LG (트윈스 — 대표색/심볼 없어 사각으로 대체)
    "sslion": "🦁",       # 삼성 라이온즈
    "lotte_giant": "🌊",  # 롯데 자이언츠 (갈매기/바다 컨셉)
    # 헤더 장식 (워크스페이스 전용) → 제거
    "duck_wave01": "",
}

_SHORTCODE_RE = re.compile(r":([a-z0-9_+-]+):")


def _replace_emoji(text: str) -> str:
    """`:shortcode:` 를 유니코드로 치환. 미매핑은 제거."""
    def sub(m: "re.Match[str]") -> str:
        code = m.group(1)
        return SHORTCODE_TO_UNICODE.get(code, "")
    return _SHORTCODE_RE.sub(sub, text)


# ── 페이지 셸 (self-contained HTML) ─────────────────────────────────────────
# Slack Canvas 와 동일 내용을 웹에서 보기 좋게. 반응형 + 한글 폰트 + 다크 대응.
SLACK_CANVAS_NOTE = (
    "이 페이지는 Slack Canvas와 동일한 KBO 현황을 웹으로 미러링한 것입니다."
)

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>오늘의 KBO · Supermatch</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 1.2rem 1rem 3rem;
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
      "Noto Sans KR", "Malgun Gothic", "Segoe UI", Roboto, sans-serif;
    line-height: 1.65;
    color: #1f2328;
    background: #ffffff;
    max-width: 760px;
    margin-inline: auto;
    -webkit-text-size-adjust: 100%;
  }}
  h1 {{ font-size: 1.5rem; margin: .2rem 0 1rem; line-height: 1.3; }}
  h2 {{ font-size: 1.2rem; margin: 1.6rem 0 .6rem; padding-top: .4rem;
        border-top: 1px solid #e3e6ea; }}
  h3 {{ font-size: 1.05rem; margin: 1rem 0 .4rem; }}
  p {{ margin: .5rem 0; }}
  a {{ color: #1f6feb; }}
  blockquote {{
    margin: .6rem 0; padding: .4rem .9rem;
    border-left: 3px solid #d0d7de; background: #f6f8fa; border-radius: 4px;
  }}
  blockquote p {{ margin: .25rem 0; }}
  table {{
    width: 100%; border-collapse: collapse; margin: .8rem 0;
    font-size: .92rem; display: block; overflow-x: auto; white-space: nowrap;
  }}
  th, td {{ border: 1px solid #d0d7de; padding: .45rem .6rem; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fbfcfd; }}
  hr {{ border: 0; border-top: 1px solid #e3e6ea; margin: 1.4rem 0; }}
  .page-note {{ font-size: .8rem; color: #6b7280; margin-bottom: 1rem; }}
  .page-foot {{ margin-top: 2.4rem; padding-top: .8rem;
                border-top: 1px solid #e3e6ea; font-size: .82rem; color: #6b7280; }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #e6edf3; background: #0d1117; }}
    h2, .page-foot {{ border-color: #21262d; }}
    h2 {{ border-top-color: #21262d; }}
    a {{ color: #58a6ff; }}
    blockquote {{ background: #161b22; border-left-color: #30363d; }}
    th {{ background: #161b22; }}
    th, td {{ border-color: #30363d; }}
    tr:nth-child(even) td {{ background: #11161d; }}
  }}
</style>
</head>
<body>
<p class="page-note">{note}</p>
{body}
<div class="page-foot">{foot}</div>
</body>
</html>
"""


def render_html_page(date: dt.date, canvas_markdown: str) -> str:
    """Canvas용 마크다운 → GitHub Pages용 self-contained HTML.

    Args:
        date: 페이지 기준 날짜 (KST).
        canvas_markdown: build_canvas_markdown(date) 결과 — Slack Canvas 본문과 동일.

    Returns:
        완성된 HTML 문자열 (docs/index.html 에 그대로 write).
    """
    md_text = _replace_emoji(canvas_markdown)
    body_html = _md.markdown(
        md_text,
        extensions=["tables", "nl2br", "sane_lists"],
        output_format="html5",
    )
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    foot = (
        f"마지막 갱신: {now} KST · "
        f'<a href="https://github.com/MyungjooKim/supermatch">supermatch</a> · '
        f"데이터: Naver 스포츠"
    )
    return _PAGE_TEMPLATE.format(note=SLACK_CANVAS_NOTE, body=body_html, foot=foot)
