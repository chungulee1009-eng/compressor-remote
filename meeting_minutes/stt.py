"""음성 → 텍스트 (faster-whisper, PC 내부에서 처리 → 회의 음성이 외부로 나가지 않음)."""
from __future__ import annotations

import os
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np

# 제조 현장 용어를 미리 알려주면 인식률이 올라간다 (설정에서 추가 가능)
DEFAULT_VOCAB = ("SAM4S, 신흥정밀, SMT, POS, KIOSK, 키오스크, PDA, EFT, 프린터, OEE, CAPA, 택타임, 리드타임, "
                 "불량률, 직행률, 재공재고, 납기, 견적, 원가, 한계이익, BOM, MES, ERP, 라인, 공정, 설비, 가동률")
SR = 16000
# 무음·잡음 구간에서 Whisper 가 지어내는 대표 문구 (유튜브 자막 학습 영향)
HALLUCINATIONS = ("시청해주셔서 감사합니다", "시청해 주셔서 감사합니다", "구독과 좋아요", "MBC 뉴스", "자막 제공",
                  "다음 영상에서", "영상 끝까지")

_model_cache: dict = {}
_model_lock = threading.Lock()  # 미리 불러오기(백그라운드)와 녹음 시작이 겹쳐도 한 번만 로드


def _load_model(size: str, force_cpu: bool = False):
    with _model_lock:
        return _load_model_locked(size, force_cpu)


def _load_model_locked(size: str, force_cpu: bool):
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
    # CPU 코어를 모두 사용 (기본값은 4개만 사용 → 다코어 PC 에서 느림)
    model = WhisperModel(size, device=device, compute_type=compute, cpu_threads=os.cpu_count() or 4)
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


def build_prompt(vocab: str = "", attendees: str = "") -> str:
    prompt = "회의 녹취록입니다. " + (vocab or DEFAULT_VOCAB)
    return prompt + (f". 참석자: {attendees}" if attendees else "")


def is_noise(seg) -> bool:
    """환각 문구 또는 '말소리 아님' 확률이 높은 구간."""
    text = seg.text.strip()
    if not text:
        return True
    if len(text) < 30 and any(h in text for h in HALLUCINATIONS):
        return True
    return getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0


_cpu_only = False  # GPU 실패 후에는 계속 CPU 사용


def get_model(size: str):
    return _load_model(size, force_cpu=_cpu_only)


def transcribe_chunk(model_size: str, audio: np.ndarray, prompt: str, beam_size: int = 1,
                     log: Callable[[str], None] = lambda s: None) -> list[tuple[float, float, str]]:
    """짧은 구간(실시간 자막용) → [(시작초, 끝초, 문장)]. GPU 실패 시 CPU 로 자동 전환.

    속도 우선: greedy 디코딩(beam 1), 단어 시각 계산 생략 → 조각 1개당 처리시간 최소화.
    """
    global _cpu_only
    kw = dict(language="ko", beam_size=beam_size, initial_prompt=prompt, condition_on_previous_text=False,
              without_timestamps=True, vad_filter=True, vad_parameters={"min_silence_duration_ms": 300})
    model = get_model(model_size)
    try:
        segs = list(model.transcribe(audio, **kw)[0])
    except RuntimeError as e:
        if model._mm_device != "cuda":
            raise
        log(f"   GPU 사용 실패({e}) → CPU 로 전환")
        _cpu_only = True
        segs = list(get_model(model_size).transcribe(audio, **kw)[0])
    return [(s.start, s.end, s.text.strip()) for s in segs if not is_noise(s)]


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
        line = "" if is_noise(seg) else f"[{fmt_ts(seg.start)}] {seg.text.strip()}"
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

    prompt = build_prompt(vocab, attendees)

    log("   음성인식 모델 준비 중...")
    model = get_model(model_size)
    log(f"   모델 준비 완료 ({model._mm_device}) — 인식 중... (CPU 는 녹음 길이의 0.3~0.5배 시간 소요)")
    try:
        lines = _run(model, audio, prompt, True, total, progress)
    except RuntimeError as e:
        if model._mm_device != "cuda":
            raise
        # 그래픽카드는 있으나 CUDA 라이브러리(cublas/cudnn)가 없는 PC → CPU 로 재시도
        log(f"   GPU 사용 실패({e}) → CPU 로 다시 인식합니다")
        global _cpu_only
        _cpu_only = True
        model = get_model(model_size)
        lines = _run(model, audio, prompt, True, total, progress)
    if not lines:
        # 무음 감지(VAD)가 작은 목소리를 통째로 잘라낸 경우 → 무음 감지 없이 재시도
        log("   무음 감지 단계에서 음성 구간을 찾지 못해, 전체 구간으로 다시 인식합니다")
        lines = _run(model, audio, prompt, False, total, progress)
    if progress:
        progress(1.0, "")
    return "\n".join(lines), total
