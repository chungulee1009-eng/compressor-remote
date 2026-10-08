"""음성 → 텍스트 (faster-whisper, PC 내부에서 처리 → 회의 음성이 외부로 나가지 않음)."""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

import numpy as np

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


def load_wav(path: str, target_sr: int = 16000) -> np.ndarray:
    """WAV → 16kHz mono float32. PyAV(av) 를 거치지 않아 av 버전 호환 문제와 무관하게 동작."""
    with wave.open(path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("16bit WAV 가 아님")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != target_sr and len(x):
        n = int(len(x) * target_sr / sr)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    return x


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(audio_path: str, model_size: str = "medium", vocab: str = "", attendees: str = "",
               progress: Callable[[float, str], None] | None = None) -> tuple[str, float]:
    """음성파일(wav/mp3/m4a 등) → ('[00:00:05] 문장\\n...', 길이초). progress(0~1, '[시각] 문장') 콜백."""
    model = _load_model(model_size)
    audio = audio_path
    if Path(audio_path).suffix.lower() == ".wav":
        try:
            audio = load_wav(audio_path)  # 프로그램 녹음파일(16bit WAV)은 직접 읽음
        except (ValueError, wave.Error):
            audio = audio_path  # 그 외 WAV 형식은 faster-whisper(av) 디코더 사용
    prompt = "회의 녹취록입니다. " + (vocab or DEFAULT_VOCAB)
    if attendees:
        prompt += f". 참석자: {attendees}"
    segments, info = model.transcribe(
        audio,
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
        line = f"[{fmt_ts(seg.start)}] {text}" if text else ""
        if line:
            lines.append(line)
        if progress:
            progress(min(1.0, seg.end / total), line)
    return "\n".join(lines), info.duration
