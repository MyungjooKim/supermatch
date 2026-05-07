"""
Claude API로 경기 박스스코어를 1~2줄 한국어로 요약합니다.
LG, 삼성, 롯데 관점에서 "왜 이겼는지 / 왜 졌는지"를 짚어줍니다.
"""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from naver_kbo import Game, TEAM_NAME

MODEL = "claude-haiku-4-5"  # 1~2문장 짧은 요약엔 Haiku로 충분 — Opus 대비 5x 저렴/빠름
SYSTEM = (
    "당신은 KBO 야구 데일리 브리핑을 작성하는 카피라이터입니다. "
    "박스스코어를 받아서, 지정된 팀 관점에서 승리 또는 패배 이유를 "
    "한국어 1~2문장으로 압축해서 전달합니다. "
    "구체적 선수 이름과 수치를 1개 이상 포함하되, 과장 없이 담백하게 씁니다. "
    "이모지는 쓰지 않습니다."
)


def _compact_box(box: dict[str, Any], team_code: str, is_home_team: bool) -> dict[str, Any]:
    """Claude에 보낼 box score 핵심 정보만 압축 — Naver record API 새 구조 기준."""
    side = "home" if is_home_team else "away"
    opp_side = "away" if is_home_team else "home"

    # etcRecords: 결승타/홈런/연속안타 등 핵심 장면 (가장 가치 높음)
    etc = box.get("etc_records") or []

    # pitchingResult: 승/패/세 투수
    pitching_result = box.get("pitching_result") or []

    # scoreBoard: 이닝별 점수 (역전/대량득점 시점 파악)
    sb = box.get("scoreboard") or {}

    # 우리 팀 타자 — 안타/타점 있는 선수만
    batters_all = (box.get("batters") or {}).get(side) or []
    key_batters = []
    for b in batters_all:
        hit = b.get("hit", 0) or 0
        rbi = b.get("rbi", 0) or 0
        hr = b.get("hr", 0) or 0
        if hit > 0 or rbi > 0 or hr > 0:
            key_batters.append({
                "name": b.get("name") or b.get("playerName"),
                "pos": b.get("pos"),
                "hit": hit, "rbi": rbi, "hr": hr,
                "run": b.get("run", 0),
                "hra": b.get("hra"),
            })

    # 우리 팀 투수 — 모든 등판 투수
    pitchers_all = (box.get("pitchers") or {}).get(side) or []
    key_pitchers = []
    for p in pitchers_all:
        key_pitchers.append({
            "name": p.get("name"),
            "inn": p.get("inn"),
            "er": p.get("er", 0),
            "kk": p.get("kk", 0),
            "hit": p.get("hit", 0),
            "wls": p.get("wls", ""),
            "era": p.get("era"),
        })

    return {
        "score_by_inning": sb.get("inn"),
        "rheb": sb.get("rheb"),
        "key_moments": etc,
        "decision_pitchers": pitching_result,
        "our_batters": key_batters[:8],  # 너무 길면 잘라냄
        "our_pitchers": key_pitchers,
        "team_stats": (box.get("team_stats") or {}).get(side),
        "opp_team_stats": (box.get("team_stats") or {}).get(opp_side),
    }


def summarize_game_for_team(
    game: Game,
    box: dict[str, Any],
    team_code: str,
    client: anthropic.Anthropic | None = None,
) -> str:
    """game을 team_code 관점에서 요약합니다. 이긴 경우/진 경우/무승부 톤이 다릅니다."""
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    won = game.winner_code() == team_code
    drew = game.winner_code() is None and game.is_finished
    team_name = TEAM_NAME.get(team_code, team_code)
    opponent_name = TEAM_NAME.get(game.opponent_of(team_code), game.opponent_of(team_code))
    my_score = game.score_for(team_code)
    opp_score = game.score_for(game.opponent_of(team_code))

    if drew:
        verdict = "무승부"
    elif won:
        verdict = "승리"
    else:
        verdict = "패배"

    is_home = team_code == game.home_code
    compact = _compact_box(box, team_code, is_home)

    user_prompt = f"""다음은 {game.game_date} KBO 경기 데이터입니다.

대상 팀: {team_name}
상대: {opponent_name}
스코어: {team_name} {my_score} - {opp_score} {opponent_name}
결과: {verdict}

핵심 데이터 ({team_name} 관점):
{json.dumps(compact, ensure_ascii=False, indent=2)[:5000]}

위 데이터를 바탕으로, {team_name} 관점에서 {verdict}의 핵심 이유를
1~2문장으로 요약해주세요. 선수 이름과 수치를 한 개 이상 포함하세요.
key_moments(결승타/홈런 등)가 있으면 우선 활용하세요.
다른 설명 없이 요약 문장만 출력합니다."""

    msg = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def no_game_message(team_code: str, client: anthropic.Anthropic | None = None) -> str:
    """경기 없는 날의 짧은 응원 메시지. (캐싱하면 API 호출 없이 재사용 가능)"""
    presets = {
        "LG": "오늘은 휴식. 잠실의 깃발은 내일을 향해 펄럭입니다.",
        "SS": "라이온즈는 숨을 고릅니다. 다음 포효가 더 깊을 거예요.",
        "LT": "사직은 잠시 조용합니다. 다음 경기, 다시 갈매기 떼가 날아오를 차례.",
    }
    return presets.get(team_code, "오늘은 경기가 없습니다. 다음 경기를 기다려요.")
