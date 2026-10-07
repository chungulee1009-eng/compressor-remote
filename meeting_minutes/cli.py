"""명령줄 실행 (녹음파일 일괄 처리용).

예) python -m meeting_minutes.cli 회의.m4a --title "주간 생산회의" --attendees "김과장, 박대리"
    python -m meeting_minutes.cli --transcript 전사문.txt --title "SMT 이전 검토" --formats xlsx docx pdf
    python -m meeting_minutes.cli --actions        # 미결 Action Item 목록
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from . import config, pipeline
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meeting_minutes", description="SAM4S AI 회의록")
    ap.add_argument("audio", nargs="?", help="음성파일 (wav/mp3/m4a ...)")
    ap.add_argument("--transcript", help="음성인식 대신 사용할 전사문 텍스트 파일 (UTF-8)")
    ap.add_argument("--title", default="회의")
    ap.add_argument("--date", default=date.today().isoformat(), help="회의일 YYYY-MM-DD (기한 계산 기준)")
    ap.add_argument("--attendees", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--formats", nargs="+", default=["xlsx", "docx", "pdf"], choices=list(pipeline.FORMATS))
    ap.add_argument("--offline", action="store_true", help="AI 없이 규칙 기반으로만 작성")
    ap.add_argument("--model", help="Whisper 모델 (small/medium/large-v3)")
    ap.add_argument("--out", help="출력 폴더")
    ap.add_argument("--actions", action="store_true", help="미결 Action Item 목록 출력")
    a = ap.parse_args(argv)

    s = config.load()
    if a.offline:
        s["use_ai"] = False
    if a.model:
        s["whisper_model"] = a.model
    store = Store(s["db_path"])

    if a.actions:
        for x in store.list_actions(only_open=True):
            print(f"[{x['display_status']}] {x['due_date'] or '-':10}  {x['assignee']:8}  {x['task']}  ({x['meeting_title']})")
        return 0
    if not a.audio and not a.transcript:
        ap.error("음성파일 또는 --transcript 가 필요합니다.")

    transcript = Path(a.transcript).read_text(encoding="utf-8") if a.transcript else ""
    mid = pipeline.process(store, s, title=a.title, meeting_date=date.fromisoformat(a.date), attendees=a.attendees,
                           location=a.location, audio_path=a.audio or "", transcript=transcript,
                           progress=lambda p, t: print(f"\r  음성인식 {p * 100:5.1f}%", end="", flush=True))
    print()
    for p in pipeline.export(store, mid, a.out or s["output_dir"], tuple(a.formats)):
        print("저장:", p)
    m = store.get_meeting(mid)
    print("\n■ Executive Summary")
    for ln in m["minutes"].get("summary", []):
        print("  -", ln)
    print(f"\n■ Action Item {len(m['actions'])}건")
    for x in m["actions"]:
        print(f"  {x['assignee']:8} {x['task']}  (기한 {x['due_date'] or x['due_text'] or '-'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
