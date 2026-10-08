"""녹음 중 실시간 자막 — 2단계(결합안).

① 미리보기: 스트리밍 모델(sherpa-onnx)이 0.1초 단위로 즉시 글자를 보여줌 (번역기처럼, 회색)
② 확정: 말이 끊기면(쉼 감지) 그 조각을 Whisper 로 인식해 띄어쓰기된 정확한 문장으로 교체

시각은 녹음파일 기준(일시정지 구간 제외)이라 전체 재인식 결과와 같은 타임라인이다.
미리보기 모델이 없거나 실패하면 ②만 동작하고, Whisper 가 실패하면 미리보기 글자를 그대로 쓴다.
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
QUIET_FRAMES = 4  # 100ms × 4 = 0.4초 조용하면 말 끊김으로 판단 (짧으면 문장 중간에서 잘림)
PREVIEW_SETTLE = 5  # 미리보기는 마지막 글자가 0.3~0.5초 늦게 나옴 → 경계 후 0.5초 더 듣고 조각 글자 확정

_noop = lambda *a: None  # noqa: E731


def _to16k(x: np.ndarray, sr: int) -> np.ndarray:
    x = x.astype(np.float32) / 32768.0
    if sr != stt.SR and len(x):
        m = int(len(x) * stt.SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, m), np.arange(len(x)), x).astype(np.float32)
    return x


class LiveTranscriber(threading.Thread):
    """이벤트: on_partial(글자) 진행 중 미리보기 / on_pending(id, 줄) 끊긴 조각의 미리보기 /
    on_final(id, 줄) Whisper 확정 문장(빈 줄이면 해당 미리보기 삭제)."""

    def __init__(self, samplerate: int, model_size: str, prompt: str,
                 on_final: Callable[[int, str], None] = _noop, on_partial: Callable[[str], None] = _noop,
                 on_pending: Callable[[int, str], None] = _noop, on_status: Callable[[str], None] = _noop,
                 log: Callable[[str], None] = _noop, q: queue.Queue | None = None,
                 preview_factory: Callable[[Callable[[str], None]], object] | None = None):
        super().__init__(daemon=True)
        self.sr = samplerate
        self.frame = max(1, samplerate // 10)
        self.model_size = model_size
        self.base_prompt = prompt
        self.on_final, self.on_partial, self.on_pending = on_final, on_partial, on_pending
        self.on_status, self.log = on_status, log
        self.q: queue.Queue[bytes | None] = q if q is not None else queue.Queue()
        self.preview_factory = preview_factory
        self.preview = None           # 준비되면 Preview 객체 (백그라운드에서 다운로드·로드)
        self.finals: dict[int, str] = {}
        self.error: Exception | None = None
        self.last_proc_sec = 0.0      # 직전 조각 Whisper 인식 시간 (속도 진단용)
        self._buf = np.zeros(0, dtype=np.int16)
        self._offset = 0              # 조각으로 넘긴 샘플 수 (= 다음 조각의 시작 시각)
        self._cid = 0
        self._wq: queue.Queue = queue.Queue()
        self._backlog = 0.0           # Whisper 대기 중인 음성 길이(초)
        self._last_final = ""
        self._whisper_ok = True
        self._pv_note = ""            # 미리보기 준비 상태 (다운로드 % 등)
        self._pv_synced = False       # 미리보기에 현재 조각 앞부분까지 넣었는지
        self._settle: list = []       # [cid, start, 남은 블록 수, 조각을 Whisper 에 보냈는지]
        self._ptexts: dict[int, str] = {}  # 조각별 미리보기 글자 (Whisper 실패 시 대체용)

    # ------------------------------------------------------------ 외부 API
    def finish(self) -> None:
        """녹음 종료: 남은 음성까지 처리(미리보기 + 확정)하고 스레드 종료."""
        self.q.put(None)

    def text(self) -> str:
        return "\n".join(v for _, v in sorted(self.finals.items()) if v)

    @property
    def lines(self) -> list[str]:
        return [v for _, v in sorted(self.finals.items()) if v]

    # ------------------------------------------------------------ 미리보기 준비
    def _prepare_preview(self):
        try:
            def note(msg: str):
                self._pv_note = msg
                self._status()
            note("미리보기 모델 준비 중...")
            self.preview = self.preview_factory(note)
            self.log("   미리보기 자막 준비 완료 (말하는 도중 표시)")
        except Exception as e:
            self.log(f"   미리보기 자막 사용 안 함 ({type(e).__name__}: {e}) — 문장 확정 자막만 표시")
        self._pv_note = ""
        self._status()

    def _status(self):
        speed = f" · 문장 확정 {self.last_proc_sec:.1f}초" if self.last_proc_sec else ""
        mode = "미리보기+확정" if self.preview else "확정 자막"
        lag = f" · 확정 대기 {self._backlog:.0f}초" if self._backlog > MAX_SEC else ""
        note = f" · {self._pv_note}" if self._pv_note else ""
        self.on_status(f"실시간 자막({mode}): 듣는 중{speed}{lag}{note}")

    # ------------------------------------------------------------ 1단계: 분배 (빠름)
    def run(self):
        worker = threading.Thread(target=self._whisper_loop, daemon=True)
        worker.start()
        if self.preview_factory:
            threading.Thread(target=self._prepare_preview, daemon=True).start()
        try:
            self._status()
            done = False
            while not done:
                try:
                    item = self.q.get(timeout=0.3)
                except queue.Empty:
                    continue
                items = [item]
                while True:
                    try:
                        items.append(self.q.get_nowait())
                    except queue.Empty:
                        break
                for it in items:
                    if it is None:
                        done = True
                        continue
                    block = np.frombuffer(it, dtype=np.int16)
                    self._buf = np.concatenate([self._buf, block])
                    pv = self.preview
                    if pv is not None:
                        try:
                            # 준비가 늦게 끝난 경우: 이번 조각을 처음부터 넣어 문장 앞부분을 놓치지 않음
                            feed = block if self._pv_synced else self._buf
                            self._pv_synced = True
                            self.on_partial(pv.accept(_to16k(feed, self.sr)))
                        except Exception as e:
                            self.preview = None
                            self.log(f"   미리보기 자막 중단: {e}")
                    if self._settle:
                        self._settle[2] -= 1
                        if self._settle[2] <= 0:
                            self._close_preview()
                    # 블록마다 경계 확인 → 음성이 몰려 들어와도 미리보기 글자가 제 조각에 붙음
                    while (cut := self._find_cut(final=False)) > 0:
                        self._cut(cut)
                if done:
                    while (cut := self._find_cut(final=True)) > 0:
                        self._cut(cut, last=cut >= len(self._buf))
                    self._close_preview(final=True)
                if not done:
                    self._status()
        except Exception as e:  # 실시간 자막 실패 → 녹음 종료 후 전체 인식으로 대체
            self.error = e
            self.log(f"   실시간 자막 오류 → 녹음 종료 후 전체 인식으로 대체: {type(e).__name__}: {e}")
            self.on_status("실시간 자막: 오류 (종료 후 전체 인식)")
        finally:
            self._wq.put(None)
            worker.join()
            if not self.error:
                self.on_status("실시간 자막: 완료")

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
        # 하위 5% = 쉼 소리 크기 (쉼이 짧아 버퍼의 일부여도 잡히도록 낮은 백분위 사용)
        noise, speech = np.percentile(rms, 5), np.percentile(rms, 90)
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

    def _cut(self, cut: int, last: bool = False) -> None:
        chunk, self._buf = self._buf[:cut], self._buf[cut:]
        start = self._offset / self.sr
        self._offset += cut
        self._cid += 1
        cid = self._cid
        x = _to16k(chunk, self.sr)
        peak_db, _ = stt.level_db(x)
        to_whisper = not (len(x) < stt.SR * 0.3 or peak_db < -50)  # 무음 조각은 건너뜀 (환각 방지)
        if self.preview is not None:
            self._close_preview()  # 직전 조각 미리보기가 아직 열려 있으면 먼저 닫음
            self._settle = [cid, start, PREVIEW_SETTLE, to_whisper]
            if last:
                self._close_preview(final=True)
        if to_whisper:
            self._backlog += len(x) / stt.SR
            self._wq.put((cid, start, x))

    def _close_preview(self, final: bool = False) -> None:
        """경계 후 0.5초 더 들은 시점에 조각의 미리보기 글자를 확정해 회색 줄로 표시."""
        pv, st = self.preview, self._settle
        if pv is None:
            self._settle = []
            return
        try:
            if not st:
                if final:
                    pv.cut(final=True)
                return
            self._settle = []
            cid, start, _, sent = st
            ptext, was_reset = pv.cut(final=final)  # 흐름은 이어가고 경계만 표시 (문맥 유지)
            if was_reset and len(self._buf):  # 흐름을 새로 시작했으면 남은 음성을 다시 넣음
                self.on_partial(pv.accept(_to16k(self._buf, self.sr)))
            else:
                self.on_partial(pv.partial())
            self._ptexts[cid] = ptext
            if not ptext:
                return
            line = f"[{stt.fmt_ts(start)}] {ptext}"
            if not sent:  # Whisper 로 보내지 않은 조각 → 미리보기 글자를 그대로 확정
                self.finals[cid] = line
                self.on_final(cid, line)
            elif cid not in self.finals:  # 이미 확정됐으면 회색 줄 생략
                self.on_pending(cid, line)
        except Exception as e:
            self.preview = None
            self.log(f"   미리보기 자막 중단: {e}")

    # ------------------------------------------------------------ 2단계: Whisper 확정 (느림)
    def _whisper_loop(self):
        if self._whisper_ok:
            try:
                stt.get_model(self.model_size)
            except Exception as e:
                self._whisper_ok = False
                self.log(f"   문장 확정 모델 사용 불가 → 미리보기 글자를 그대로 사용: {type(e).__name__}: {e}")
        while (job := self._wq.get()) is not None:
            cid, start, x = job
            text = ""
            if self._whisper_ok:
                try:
                    xn, _ = stt.normalize(x)
                    # 직전 문장을 힌트로 주면 조각 경계에서도 문맥이 이어짐
                    prompt = (self.base_prompt + " " + self._last_final)[-400:]
                    t0 = time.perf_counter()
                    results = stt.transcribe_chunk(self.model_size, xn, prompt, log=self.log)
                    self.last_proc_sec = time.perf_counter() - t0
                    text = " ".join(t for _, _, t in results).strip()
                except Exception as e:
                    self.log(f"   문장 확정 실패(미리보기 글자 사용): {type(e).__name__}: {e}")
            if not text and self.preview is not None:
                for _ in range(10):  # 미리보기 글자가 확정될 때까지 잠시 대기 (최대 0.5초)
                    if cid in self._ptexts:
                        break
                    time.sleep(0.05)
            text = text or self._ptexts.get(cid, "")  # Whisper 가 못 들은 말은 미리보기 결과로 보존
            line = f"[{stt.fmt_ts(start)}] {text}" if text else ""
            if text:
                self._last_final = text
            self.finals[cid] = line
            self._backlog = max(0.0, self._backlog - len(x) / stt.SR)
            self.on_final(cid, line)
