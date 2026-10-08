"""음성파일/전사문 → 회의록 생성 → DB 저장 → 파일 출력 (GUI·CLI 공통)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

from . import exporters, summarizer
from .storage import Store

FORMATS = {"xlsx": exporters.export_excel, "docx": exporters.export_word, "pdf": exporters.export_pdf,
           "txt": exporters.export_txt}


def process(store: Store, settings: dict, *, title: str, meeting_date: date, attendees: str = "", location: str = "",
            audio_path: str = "", transcript: str = "", duration_sec: float = 0.0,
            log: Callable[[str], None] = print,
            progress: Callable[[float, str], None] | None = None) -> int:
    """회의 1건 처리 후 meeting_id 반환. transcript 를 주면 음성인식을 건너뛴다."""
    if not transcript:
        if not audio_path:
            raise ValueError("음성파일 또는 전사문이 필요합니다.")
        from . import stt
        log(f"[1/3] 음성인식 시작 (모델: {settings['whisper_model']}) — 최초 1회는 모델 다운로드로 시간이 걸립니다.")
        transcript, duration_sec = stt.transcribe(audio_path, settings["whisper_model"], settings.get("vocab", ""),
                                                  attendees, progress, log=log)
        log(f"[1/3] 음성인식 완료: {len(transcript.splitlines())}문장, {int(duration_sec // 60)}분")
    if not transcript.strip():
        raise RuntimeError("인식된 음성이 없습니다. 마이크 입력/녹음파일을 확인하세요.")

    log("[2/3] 회의록 작성 중 (AI)..." if settings.get("use_ai") else "[2/3] 회의록 작성 중 (규칙 기반)...")
    minutes = summarizer.summarize(transcript, meeting_date, title, attendees, settings.get("use_ai", True),
                                   settings.get("ai_effort", "medium"), log)
    n = len(minutes.get("action_items", []))
    log(f"[2/3] 회의록 완료 — 결정 {len(minutes.get('decisions', []))}건, Action Item {n}건 ({minutes.get('_engine')})")

    mid = store.save_meeting(title, meeting_date, attendees, location, audio_path, duration_sec, transcript, minutes)
    log(f"[3/3] 저장 완료 (회의 #{mid})")
    return mid


def export(store: Store, meeting_id: int, out_dir: str | Path, fmts: tuple[str, ...] = ("xlsx",)) -> list[Path]:
    m = store.get_meeting(meeting_id)
    if not m:
        raise KeyError(meeting_id)
    return [FORMATS[f](m, out_dir) for f in fmts]
