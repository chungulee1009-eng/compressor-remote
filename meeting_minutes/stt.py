"""음성 → 텍스트 (faster-whisper, PC 내부에서 처리 → 회의 음성이 외부로 나가지 않음)."""
from __future__ import annotations

from typing import Callable

# 제조 현장 용어를 미리 알려주면 인식률이 올라간다 (설정에서 추가 가능)
DEFAULT_VOCAB = ("SAM4S, 신흥정밀, SMT, POS, KIOSK, 키오스크, PDA, EFT, 프린터, OEE, CAPA, 택타임, 리드타임, "
                 "불량률, 직행률, 재공재고, 납기, 견적, 원가, 한계이익, BOM, MES, ERP, 라인, 공정, 설비, 가동률")

_model_cache: dict = {}


def _load_model(size: str):
    if size in _model_cache:
        return _model_cache[size]
    from faster_whisper import WhisperModel

    device, compute = "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, compute = "cuda", "float16"
    except Exception:
        pass
    model = WhisperModel(size, device=device, compute_type=compute)
    _model_cache[size] = model
    return model


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(audio_path: str, model_size: str = "medium", vocab: str = "", attendees: str = "",
               progress: Callable[[float, str], None] | None = None) -> tuple[str, float]:
    """음성파일(wav/mp3/m4a 등) → ('[00:00:05] 문장\\n...', 길이초). progress(0~1, 최근문장) 콜백."""
    model = _load_model(model_size)
    prompt = "회의 녹취록입니다. " + (vocab or DEFAULT_VOCAB)
    if attendees:
        prompt += f". 참석자: {attendees}"
    segments, info = model.transcribe(
        audio_path,
        language="ko",
        vad_filter=True,  # 무음 구간 건너뛰기 → 속도↑, 환각 문장↓
        vad_parameters={"min_silence_duration_ms": 700},
        beam_size=5,
        initial_prompt=prompt,
        condition_on_previous_text=False,  # 같은 문장 반복 환각 방지
    )
    lines = []
    total = info.duration or 1.0
    for seg in segments:
        text = seg.text.strip()
        if text:
            lines.append(f"[{fmt_ts(seg.start)}] {text}")
        if progress:
            progress(min(1.0, seg.end / total), text)
    return "\n".join(lines), info.duration
