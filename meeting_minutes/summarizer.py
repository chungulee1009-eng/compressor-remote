"""회의 전사문 → 구조화 회의록 (Claude API, 실패/미설정 시 규칙 기반 대체)."""
from __future__ import annotations

import json
import os
from datetime import date

from . import rules

MODEL = "claude-opus-5-5"

# 회의록 JSON 스키마 (structured outputs 로 형식 보장)
_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
MINUTES_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {**_STR, "description": "회의 목적 1문장"},
        "summary": {**_STR_LIST, "description": "경영진 보고용 핵심 요약 3줄 (결론·숫자 우선)"},
        "discussions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": _STR, "content": _STR},
                "required": ["topic", "content"],
                "additionalProperties": False,
            },
        },
        "decisions": _STR_LIST,
        "issues": _STR_LIST,
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "assignee": {**_STR, "description": "담당자 (예: 김과장). 불명확하면 '미정'"},
                    "task": {**_STR, "description": "명사형으로 끝나는 구체적 업무 (예: SMT 업체 3곳 견적 확보)"},
                    "due_text": {**_STR, "description": "발언 속 기한 원문 (예: 다음 주 화요일까지). 없으면 빈 문자열"},
                    "due_date": {**_STR, "description": "YYYY-MM-DD. 회의일 기준으로 환산, 없으면 빈 문자열"},
                    "priority": {"type": "string", "enum": ["높음", "보통", "낮음"]},
                },
                "required": ["assignee", "task", "due_text", "due_date", "priority"],
                "additionalProperties": False,
            },
        },
        "pending": {**_STR_LIST, "description": "결론 나지 않은 미결 사항"},
        "next_meeting": {**_STR, "description": "차기 회의 일정/안건. 없으면 빈 문자열"},
    },
    "required": ["purpose", "summary", "discussions", "decisions", "issues", "action_items", "pending", "next_meeting"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 제조업체(신흥정밀 SAM4S: POS·KIOSK·PDA·프린터·EFT 단말 생산) 경영진 보고용 회의록 작성자입니다.
음성인식(STT)으로 만든 회의 전사문을 받아 회의록을 작성합니다.

작성 원칙
- 전사문에 실제로 나온 내용만 씁니다. 추측으로 담당자·기한·숫자를 만들지 않습니다.
- 결론 먼저, 숫자 중심, 간결한 개조식(~함, ~필요, ~예정)으로 씁니다.
- STT 오인식으로 보이는 단어는 문맥상 명백할 때만 바로잡습니다 (예: 에스엠티 → SMT, 오이이 → OEE, 캐파 → CAPA).
- action_items: 누군가에게 지시·요청했거나 누군가 하겠다고 약속한 업무만 넣습니다.
  담당자는 발언 속 호칭 그대로(김과장, 박민수 대리) 쓰고, 지시 대상이 불명확하면 '미정'.
  기한은 회의일 기준으로 계산해 due_date(YYYY-MM-DD)에 넣습니다. 주는 월요일 시작입니다.
  '다음 주 화요일' = 회의일이 속한 주의 다음 주 화요일. 기한 언급이 없으면 두 필드 모두 빈 문자열.
- priority: 납기·품질·고객 클레임·안전 관련이거나 기한이 1주 이내면 '높음'.
- decisions 는 확정된 사항만, 결론이 안 난 것은 pending 으로 보냅니다.
- issues 는 생산성·불량률·납기·원가·재고·설비 관점의 문제점과 원인을 씁니다."""


def _weekday_ko(d: date) -> str:
    return "월화수목금토일"[d.weekday()]


def build_user_message(transcript: str, meeting_date: date, title: str, attendees: str) -> str:
    return (
        f"회의명: {title or '(미입력)'}\n"
        f"회의일: {meeting_date.isoformat()} ({_weekday_ko(meeting_date)}요일)\n"
        f"참석자: {attendees or '(미입력)'}\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )


def normalize(minutes: dict, meeting_date: date) -> dict:
    """AI/규칙 결과 공통 후처리: 기한 날짜 보정, 빈 담당자 처리."""
    for a in minutes.get("action_items", []):
        a["assignee"] = (a.get("assignee") or "").strip() or "미정"
        due = (a.get("due_date") or "").strip()
        try:
            date.fromisoformat(due)
        except ValueError:
            d = rules.parse_due(a.get("due_text", ""), meeting_date)
            a["due_date"] = d.isoformat() if d else ""
        a.setdefault("priority", "보통")
    return minutes


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def summarize_with_claude(transcript: str, meeting_date: date, title: str = "", attendees: str = "",
                          effort: str = "medium") -> dict:
    import anthropic

    client = anthropic.Anthropic()
    # 긴 회의(1~2시간)도 타임아웃 없이 받도록 스트리밍 사용
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(transcript, meeting_date, title, attendees)}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": MINUTES_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("AI 가 이 회의 내용 처리를 거절했습니다 (규칙 기반으로 대체).")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("회의록이 너무 길어 AI 응답이 잘렸습니다 (규칙 기반으로 대체).")
    text = next(b.text for b in response.content if b.type == "text")
    minutes = json.loads(text)
    minutes["_engine"] = response.model
    return minutes


def summarize(transcript: str, meeting_date: date, title: str = "", attendees: str = "",
              use_ai: bool = True, effort: str = "medium", log=print) -> dict:
    """회의록 생성. AI 사용 불가/실패 시 규칙 기반 결과로 대체하고 사유를 log 로 남긴다."""
    if use_ai and has_api_key():
        try:
            return normalize(summarize_with_claude(transcript, meeting_date, title, attendees, effort), meeting_date)
        except Exception as e:  # 네트워크/키/거절 등 어떤 실패든 회의록은 반드시 나오게
            log(f"[AI 실패 → 규칙 기반 대체] {type(e).__name__}: {e}")
    elif use_ai:
        log("[안내] ANTHROPIC_API_KEY 가 없어 규칙 기반(오프라인)으로 회의록을 만듭니다.")
    return normalize(rules.offline_minutes(transcript, meeting_date, title), meeting_date)
