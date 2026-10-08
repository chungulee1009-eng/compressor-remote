"""PC 마이크 녹음 → WAV (16kHz mono). 녹음 중에도 파일에 바로 기록해 장시간 회의/비정상 종료에도 안전."""
from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

TARGET_SR = 16000  # Whisper 입력 표준


class Recorder:
    def __init__(self, device: int | None = None):
        self.device = device
        self.path: Path | None = None
        self._stream = None
        self._wav: wave.Wave_write | None = None
        self._q: queue.Queue[bytes] = queue.Queue()
        self._writer: threading.Thread | None = None
        self._paused = False
        self._started = 0.0
        self._paused_total = 0.0
        self._paused_at = 0.0
        self.level = 0.0  # 0~1 입력 레벨(레벨미터용)
        self.samplerate = TARGET_SR
        self.live_q: queue.Queue | None = None  # 실시간 자막용 (LiveTranscriber.q)

    @staticmethod
    def list_input_devices() -> list[tuple[int, str]]:
        import sounddevice as sd
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def paused(self) -> bool:
        return self._paused

    def elapsed(self) -> float:
        if not self._started:
            return 0.0
        now = self._paused_at if self._paused else time.time()
        return now - self._started - self._paused_total

    def _callback(self, indata, frames, t, status):  # sounddevice 오디오 스레드
        mono = indata[:, 0] if indata.ndim > 1 else indata
        self.level = min(1.0, float(np.sqrt(np.mean(mono.astype(np.float32) ** 2))) / 8000.0)
        if not self._paused:
            data = mono.astype(np.int16).tobytes()
            self._q.put(data)
            if self.live_q is not None:
                self.live_q.put(data)

    def _write_loop(self):
        while self._stream is not None or not self._q.empty():
            try:
                chunk = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._wav.writeframes(chunk)  # 매 청크마다 헤더 갱신 → 강제 종료돼도 재생 가능

    def start(self, path: str | Path) -> None:
        import sounddevice as sd

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 16kHz 를 지원하지 않는 장치는 장치 기본 샘플레이트로 녹음(Whisper 가 자동 리샘플)
        sr = TARGET_SR
        try:
            sd.check_input_settings(device=self.device, samplerate=sr, channels=1, dtype="int16")
        except Exception:
            sr = int(sd.query_devices(self.device, "input")["default_samplerate"])
        self.samplerate = sr
        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sr)
        self._paused = False
        self._paused_total = 0.0
        self._stream = sd.InputStream(device=self.device, samplerate=sr, channels=1, dtype="int16",
                                      callback=self._callback, blocksize=int(sr * 0.1))
        self._stream.start()
        self._started = time.time()
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    def pause(self) -> None:
        if self.recording and not self._paused:
            self._paused = True
            self._paused_at = time.time()

    def resume(self) -> None:
        if self.recording and self._paused:
            self._paused_total += time.time() - self._paused_at
            self._paused = False

    def stop(self) -> tuple[Path, float]:
        dur = self.elapsed()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._writer:
            self._writer.join(timeout=5)
        if self._wav:
            self._wav.close()
            self._wav = None
        self._started = 0.0
        self.level = 0.0
        return self.path, dur
