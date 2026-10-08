"""설정 (meeting_minutes/data/settings.json)."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    "output_dir": str(APP_DIR / "회의록_출력"),
    "recordings_dir": str(DATA_DIR / "녹음"),
    "db_path": str(DATA_DIR / "meetings.db"),
    "whisper_model": "medium",      # small(빠름) / medium(권장) / large-v3(정확, 느림)
    "vocab": "",                    # 추가 용어(제품명·사람 이름 등), 비우면 기본 제조용어
    "use_ai": True,                 # False → 규칙 기반(완전 오프라인)
    "ai_effort": "medium",          # low / medium / high
    "mic_device": None,             # None = 윈도우 기본 마이크
    "live_stt": True,               # 녹음 중 실시간 자막
    "final_full_pass": False,       # 녹음 종료 후 전체 다시 인식 (정확도↑, 녹음 길이의 0.3~0.5배 시간 추가)
    "api_key": "",                  # 비우면 환경변수 ANTHROPIC_API_KEY 사용
}


def load() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    apply_api_key(s)
    return s


def save(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_api_key(s)


def apply_api_key(s: dict) -> None:
    if s.get("api_key"):
        os.environ["ANTHROPIC_API_KEY"] = s["api_key"].strip()
