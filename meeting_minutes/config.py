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
    "live_engine": "local",         # local: PC 내부(미리보기+Whisper) / google: Chrome 음성인식(즉시, 온라인)
    "live_preview": True,           # 말하는 도중 미리보기 자막 (sherpa-onnx 스트리밍, 회색 → 확정 시 교체)
    "live_model": "base",           # 실시간 자막용 모델 — base: 빠름(기본) / small: 정확도↑·느림 / medium: 정확·가장 느림
    "settings_rev": 2,
    "final_full_pass": False,       # 녹음 종료 후 전체 다시 인식 (정확도↑, 녹음 길이의 0.3~0.5배 시간 추가)
    "api_key": "",                  # 비우면 환경변수 ANTHROPIC_API_KEY 사용
}


def load() -> dict:
    s = dict(DEFAULTS)
    saved: dict = {}
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            s.update(saved)
        except (OSError, ValueError):
            pass
    if saved and saved.get("settings_rev", 1) < 2:
        # v1.4 에서 저장된 기본값(small) → 실시간 자막 속도 우선으로 base 전환 (직접 고른 값은 이후 유지)
        if s.get("live_model") == "small":
            s["live_model"] = "base"
        s["settings_rev"] = 2
    apply_api_key(s)
    return s


def save(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    apply_api_key(s)


def apply_api_key(s: dict) -> None:
    if s.get("api_key"):
        os.environ["ANTHROPIC_API_KEY"] = s["api_key"].strip()
