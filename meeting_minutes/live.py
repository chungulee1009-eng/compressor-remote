"""녹음 중 실시간 자막: 말이 끊기는 지점에서 잘라 바로 음성인식 → 문장을 즉시 화면에 표시.

Recorder 가 넣어주는 오디오 조각(int16 bytes)을 모아 4~12초 단위(말 멈춤 지점)로 인식한다.
시각은 녹음파일 기준(일시정지 구간 제외)이라 전체 재인식 결과와 같은 타임라인이다.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import numpy as np

from . import stt

MIN_SEC = 2.0    # 이보다 짧으면 자르지 않음 (너무 짧으면 문맥 부족 → 인식률↓)
MAX_SEC = 8.0    # 말이 계속 이어져도 이 길이에서 가장 조용한 지점으로 자름
QUIET_FRAMES = 3  # 100ms × 3 = 0.3초 조용하면 말 끊김으로 판단


class LiveTranscriber(threading.Thread):
    def __init__(self, samplerate: int, model_size: str, prompt: str,
                 on_line: Callable[[str], None], on_status: Callable[[str], None] = lambda s: None,
                 log: Callable[[str], None] = lambda s: None, q: queue.Queue | None = None):
        super().__init__(daemon=True)
        self.sr = samplerate
        self.frame = max(1, samplerate // 10)
        self.model_size = model_size
        self.base_prompt = prompt
        self.on_line, self.on_status, self.log = on_line, on_status, log
        self.q: queue.Queue[bytes | None] = q if q is not None else queue.Queue()
        self.lines: list[str] = []
        self.error: Exception | None = None
        self._buf = np.zeros(0, dtype=np.int16)
        self._offset = 0      # 이미 인식 처리한 샘플 수 (= 다음 조각의 시작 시각)
        self._received = 0    # 받은 총 샘플 수
        self.last_proc_sec = 0.0  # 직전 조각 인식에 걸린 시간 (속도 진단용)

    # ------------------------------------------------------------ 외부 API
    def finish(self) -> None:
        """녹음 종료: 남은 음성까지 처리하고 스레드 종료."""
        self.q.put(None)

    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def lag_sec(self) -> float:
        return (self._received - self._offset) / self.sr

    # ------------------------------------------------------------ 내부
    def run(self):
        try:
            self.on_status("실시간 자막: 음성인식 모델 준비 중...")
            stt.get_model(self.model_size)
            self.on_status("실시간 자막: 듣는 중")
            done = False
            while not done:
                try:
                    item = self.q.get(timeout=0.3)
                except queue.Empty:
                    continue
                items = [item]
                while True:  # 인식하는 동안 쌓인 조각 한 번에 받기
                    try:
                        items.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                for it in items:
                    if it is None:
                        done = True
                    else:
                        chunk = np.frombuffer(it, dtype=np.int16)
                        self._buf = np.concatenate([self._buf, chunk])
                        self._received += len(chunk)
                while (cut := self._find_cut(final=done)) > 0:
                    self._process(cut)
                lag = self.lag_sec
                if not done:
                    speed = f" · 조각 인식 {self.last_proc_sec:.1f}초" if self.last_proc_sec else ""
                    self.on_status(f"실시간 자막: 듣는 중{speed}" if lag < MAX_SEC + 3
                                   else f"실시간 자막: 처리 지연 {lag:.0f}초{speed} (설정에서 자막 모델을 base 로)")
            self.on_status("실시간 자막: 완료")
        except Exception as e:  # 실시간 자막 실패 → 녹음 종료 후 전체 인식으로 대체
            self.error = e
            self.log(f"   실시간 자막 오류 → 녹음 종료 후 전체 인식으로 대체: {type(e).__name__}: {e}")
            self.on_status("실시간 자막: 오류 (종료 후 전체 인식)")

    def _frame_rms(self, x: np.ndarray) -> np.ndarray:
        n = len(x) // self.frame
        if n == 0:
            return np.zeros(0)
        f = x[: n * self.frame].astype(np.float32).reshape(n, self.frame)
        return np.sqrt((f ** 2).mean(axis=1))

    def _find_cut(self, final: bool) -> int:
        """버퍼 앞에서부터 MIN~MAX 초 사이 첫 '말 멈춤' 지점(샘플 위치). 0 이면 아직 자르지 않음.

        인식이 밀려 버퍼가 길게 쌓인 경우에도 앞에서부터 문장 단위로 잘라낸다.
        """
        n = len(self._buf)
        if n == 0:
            return 0
        dur = n / self.sr
        if dur < MIN_SEC:
            return n if final else 0
        rms = self._frame_rms(self._buf)
        noise, speech = np.percentile(rms, 20), np.percentile(rms, 90)
        if speech > noise * 2 + 50:      # 말소리/쉼 대비가 있을 때만 쉼을 판단
            quiet = rms <= noise + 0.25 * (speech - noise)
        else:                            # 대비 없음: 전체가 무음이면 아무 데서나, 계속 말하면 쉼 없음
            quiet = np.full(len(rms), speech < 300)
        lo, hi = int(MIN_SEC * 10), min(len(rms), int(MAX_SEC * 10))
        run = 0
        for i in range(max(0, lo - QUIET_FRAMES), hi):
            run = run + 1 if quiet[i] else 0
            if run >= QUIET_FRAMES and i + 1 >= lo:
                return (i + 1) * self.frame
        if dur >= MAX_SEC:  # 말이 계속 이어짐 → MIN~MAX 구간에서 가장 조용한 지점
            seg = rms[lo:hi]
            if seg.min() > 0.5 * np.median(seg):  # 뚜렷한 틈이 없음 → 최대 길이에서 자름
                return hi * self.frame
            i = hi - 1 - int(np.argmin(seg[::-1]))  # 같은 값이면 뒤쪽(긴 조각) 선택
            return (i + 1) * self.frame
        return n if final else 0

    def _process(self, cut: int) -> None:
        chunk, self._buf = self._buf[:cut], self._buf[cut:]
        start = self._offset / self.sr
        self._offset += cut
        x = chunk.astype(np.float32) / 32768.0
        if self.sr != stt.SR:
            m = int(len(x) * stt.SR / self.sr)
            x = np.interp(np.linspace(0, len(x) - 1, m), np.arange(len(x)), x).astype(np.float32)
        peak_db, _ = stt.level_db(x)
        if len(x) < stt.SR * 0.3 or peak_db < -50:
            return  # 무음 조각은 건너뜀 (환각 문장 방지)
        x, _ = stt.normalize(x)
        # 직전 문장을 힌트로 주면 조각 경계에서도 문맥이 이어짐
        prompt = self.base_prompt + (" " + self.lines[-1].split("] ", 1)[-1] if self.lines else "")
        t0 = time.perf_counter()
        results = stt.transcribe_chunk(self.model_size, x, prompt[-400:], log=self.log)
        self.last_proc_sec = time.perf_counter() - t0
        for s, _e, text in results:
            line = f"[{stt.fmt_ts(start + s)}] {text}"
            self.lines.append(line)
            self.on_line(line)
