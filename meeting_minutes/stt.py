"""음성 → 텍스트 (faster-whisper, PC 내부에서 처리 → 회의 음성이 외부로 나가지 않음)."""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

import numpy as np

# 제조 현장 용어를 미리 알려주면 인식률이 올라간다 (설정에서 추가 가능)
DEFAULT_VOCAB = ("SAM4S, 신흥정밀, SMT, POS, KIOSK, 키오스크, PDA, EFT, 프린터, OEE, CAPA, 택타임, 리드타임, "
                 "불량률, 직행률, 재공재고, 납기, 견적, 원가, 한계이익, BOM, MES, ERP, 라인, 공정, 설비, 가동률")
SR = 16000

_model_cache: dict = {}


def _load_model(size: str, force_cpu: bool = False):
    key = (size, force_cpu)
    if key in _model_cache:
        return _model_cache[key]
    from faster_whisper import WhisperModel

    device, compute = "cpu", "int8"
    if not force_cpu:
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                device, compute = "cuda", "float16"
        except Exception:
            pass
    model = WhisperModel(size, device=device, compute_type=compute)
    model._mm_device = device  # 로그 표시용
    _model_cache[key] = model
    return model


def load_wav(path: str, target_sr: int = SR) -> np.ndarray:
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


def load_audio(path: str) -> np.ndarray:
    if Path(path).suffix.lower() == ".wav":
        try:
            return load_wav(path)  # 프로그램 녹음파일(16bit WAV)은 직접 읽음
        except (ValueError, wave.Error):
            pass
    from faster_whisper import decode_audio  # mp3/m4a 등은 av 디코더
    return decode_audio(path, sampling_rate=SR)


def level_db(x: np.ndarray) -> tuple[float, float]:
    """(peak dBFS, rms dBFS). 무음이면 -inf."""
    if not len(x):
        return float("-inf"), float("-inf")
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    to_db = lambda v: 20 * np.log10(v) if v > 0 else float("-inf")  # noqa: E731
    return to_db(peak), to_db(rms)


def normalize(x: np.ndarray, target_peak: float = 0.9, max_gain: float = 30.0) -> tuple[np.ndarray, float]:
    """작게 녹음된 음성(노트북 내장 마이크 등)을 키운다. (보정된 음성, 배율)"""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 0 or peak >= 0.5:
        return x, 1.0
    gain = min(max_gain, target_peak / peak)
    return (x * gain).astype(np.float32), gain


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _run(model, audio: np.ndarray, prompt: str, vad: bool, total: float, progress) -> list[str]:
    kw = dict(language="ko", beam_size=5, initial_prompt=prompt,
              condition_on_previous_text=False)  # 같은 문장 반복 환각 방지
    if vad:
        kw.update(vad_filter=True, vad_parameters={"min_silence_duration_ms": 700})  # 무음 건너뛰기
    segments, _ = model.transcribe(audio, **kw)
    lines = []
    for seg in segments:
        text = seg.text.strip()
        line = f"[{fmt_ts(seg.start)}] {text}" if text else ""
        if line:
            lines.append(line)
        if progress:
            progress(min(1.0, seg.end / total), line)
    return lines


def transcribe(audio_path: str, model_size: str = "medium", vocab: str = "", attendees: str = "",
               progress: Callable[[float, str], None] | None = None,
               log: Callable[[str], None] = lambda s: None) -> tuple[str, float]:
    """음성파일(wav/mp3/m4a 등) → ('[00:00:05] 문장\\n...', 길이초). progress(0~1, '[시각] 문장') 콜백."""
    audio = load_audio(audio_path)
    total = len(audio) / SR
    pk, rms = level_db(audio)
    log(f"   음성 {total:.1f}초 / 최대 {pk:.0f} dB, 평균 {rms:.0f} dB")
    if total < 0.5 or pk < -60:
        raise RuntimeError(f"녹음파일에 소리가 거의 없습니다 (최대 {pk:.0f} dB). 설정 탭에서 마이크를 확인하세요.")
    audio, gain = normalize(audio)
    if gain > 1.0:
        log(f"   녹음 음량이 작아 {gain:.1f}배 키워서 인식합니다 (마이크를 가까이 두면 인식률↑)")

    prompt = "회의 녹취록입니다. " + (vocab or DEFAULT_VOCAB)
    if attendees:
        prompt += f". 참석자: {attendees}"

    log("   음성인식 모델 준비 중...")
    model = _load_model(model_size)
    log(f"   모델 준비 완료 ({model._mm_device}) — 인식 중... (CPU 는 녹음 길이의 0.3~0.5배 시간 소요)")
    try:
        lines = _run(model, audio, prompt, True, total, progress)
    except RuntimeError as e:
        if model._mm_device != "cuda":
            raise
        # 그래픽카드는 있으나 CUDA 라이브러리(cublas/cudnn)가 없는 PC → CPU 로 재시도
        log(f"   GPU 사용 실패({e}) → CPU 로 다시 인식합니다")
        model = _load_model(model_size, force_cpu=True)
        lines = _run(model, audio, prompt, True, total, progress)
    if not lines:
        # 무음 감지(VAD)가 작은 목소리를 통째로 잘라낸 경우 → 무음 감지 없이 재시도
        log("   무음 감지 단계에서 음성 구간을 찾지 못해, 전체 구간으로 다시 인식합니다")
        lines = _run(model, audio, prompt, False, total, progress)
    if progress:
        progress(1.0, "")
    return "\n".join(lines), total
