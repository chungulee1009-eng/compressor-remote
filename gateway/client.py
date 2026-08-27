"""게이트웨이 측 mini-SLMP 클라이언트. 실제 PLC/시뮬레이터 공통."""
from __future__ import annotations

import socket
import struct

from gateway import protocol as slmp


class SlmpClient:
    def __init__(self, host: str, port: int, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def _ensure(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        return s

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _txn(self, frame: bytes) -> bytes:
        s = self._ensure()
        try:
            s.sendall(frame)
            hdr = self._recvn(s, 9)
            rlen = struct.unpack("<H", hdr[7:9])[0]
            rest = self._recvn(s, rlen)
            return hdr + rest
        except (OSError, ConnectionError):
            self.close()
            raise

    @staticmethod
    def _recvn(s: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("연결 종료")
            buf += chunk
        return buf

    def read_words(self, head: int, count: int) -> list[int]:
        resp = self._txn(slmp.build_read_request(head, count))
        return slmp.decode_words(slmp.parse_response(resp))

    def write_words(self, head: int, words: list[int]) -> None:
        resp = self._txn(slmp.build_write_request(head, words))
        slmp.parse_response(resp)  # 엔드코드 검증
