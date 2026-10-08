"""말하는 도중 즉시 보이는 '미리보기 자막' (sherpa-onnx 한국어 스트리밍 모델, PC 내부 처리).

번역기처럼 말하는 동안 글자가 계속 나온다(0.1초 단위 처리, 음성 길이의 약 5% 계산량).
띄어쓰기가 없고 정확도가 Whisper 보다 낮으므로 화면 미리보기용으로만 쓰고,
말이 끊기면 Whisper 결과로 교체한다.
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

MODEL_NAME = "sherpa-onnx-streaming-zipformer-korean-2024-06-16"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
FILES = {  # 압축파일(418MB) 중 필요한 것만 보관 (약 140MB)
    "tokens": "tokens.txt",
    "encoder": "encoder-epoch-99-avg-1.int8.onnx",
    "decoder": "decoder-epoch-99-avg-1.onnx",
    "joiner": "joiner-epoch-99-avg-1.int8.onnx",
}
TAIL_PAD_SEC = 0.5  # 끝에 무음을 덧대야 마지막 1~2글자가 잘리지 않음 (실측)
_LEAD = " .,?!"     # 앞 조각의 마침표가 늦게 나와 다음 조각 앞에 붙는 것 제거


def model_dir() -> Path:
    """OneDrive 동기화 폴더 밖(로컬 앱데이터)에 저장."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "SAM4S_MeetingMinutes" / "models" / MODEL_NAME


def is_installed(d: Path | None = None) -> bool:
    d = d or model_dir()
    return all((d / f).exists() for f in FILES.values())


def download(progress: Callable[[str], None] = lambda s: None, d: Path | None = None) -> Path:
    """최초 1회 모델 다운로드 (GitHub, 약 418MB) → 필요한 파일만 추출."""
    d = d or model_dir()
    if is_installed(d):
        return d
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "download.tar.bz2.part"

    def hook(blocks, bs, total):
        if total > 0 and blocks % 200 == 0:
            progress(f"미리보기 모델 다운로드 {min(100, blocks * bs * 100 // total)}% (최초 1회, 약 420MB)")

    urllib.request.urlretrieve(MODEL_URL, tmp, hook)
    progress("미리보기 모델 압축 해제 중...")
    wanted = set(FILES.values())
    with tarfile.open(tmp, "r:bz2") as tar:
        for m in tar:
            name = Path(m.name).name
            if m.isfile() and name in wanted:  # 경로를 무시하고 파일명만 사용 → 폴더 밖으로 풀리지 않음
                with tar.extractfile(m) as src, open(d / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    tmp.unlink(missing_ok=True)
    if not is_installed(d):
        raise RuntimeError("미리보기 모델 파일이 압축파일에 없습니다.")
    return d


class Preview:
    """연속 스트리밍 인식기. 녹음 내내 하나의 흐름으로 인식해 문장 중간에서 조각이 잘려도 문맥 유지.

    accept(): 0.1초씩 넣으면 '현재 조각'의 글자 반환 (말하는 중 미리보기)
    cut():    조각 경계 표시 → 그 조각의 글자 반환. 글자가 많이 쌓이면 흐름을 비움(끝 글자 보존)
    """
    MAX_CHARS = 400  # 흐름에 쌓인 글자가 이보다 많아지면 조각 경계에서 새 흐름으로

    def __init__(self, d: Path | None = None, threads: int = 2):
        import sherpa_onnx

        d = d or model_dir()
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / FILES["tokens"]), encoder=str(d / FILES["encoder"]),
            decoder=str(d / FILES["decoder"]), joiner=str(d / FILES["joiner"]),
            num_threads=threads, sample_rate=16000, feature_dim=80, decoding_method="greedy_search")
        self.reset()

    def reset(self) -> None:
        """새 녹음 시작 시 이전 상태 비우기 (엔진은 재사용 → 로딩 시간 없음)."""
        self.stream = self.rec.create_stream()
        self._mark = 0  # 직전 조각 경계까지의 글자 수 (greedy 결과는 덧붙기만 하므로 위치로 구분)

    def _full(self) -> str:
        r = self.rec.get_result(self.stream)
        return r if isinstance(r, str) else getattr(r, "text", "")

    def _decode(self) -> None:
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)

    def accept(self, x16k: np.ndarray) -> str:
        self.stream.accept_waveform(16000, x16k.astype(np.float32))
        self._decode()
        return self.partial()

    def partial(self) -> str:
        return self._full()[self._mark:].lstrip(_LEAD).strip()

    def cut(self, final: bool = False) -> tuple[str, bool]:
        """(이 조각의 글자, 흐름을 새로 시작했는지). 새로 시작했으면 남은 음성을 다시 넣어야 함."""
        full = self._full()
        if final or len(full) > self.MAX_CHARS:
            self.stream.accept_waveform(16000, np.zeros(int(16000 * TAIL_PAD_SEC), np.float32))
            self.stream.input_finished()
            self._decode()
            text = self._full()[self._mark:].lstrip(_LEAD).strip()
            self.reset()
            return text, True
        prev, self._mark = self._mark, len(full)
        return full[prev:].lstrip(_LEAD).strip(), False
