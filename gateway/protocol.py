"""mini-SLMP: 미쓰비시 SLMP(SeamLess Message Protocol) 3E 프레임 바이너리의
축약 구현.

실제 SLMP 3E 바이너리와 헤더/커맨드 구조를 동일하게 맞춘 학습·프로토타입용이다.
- Batch Read  (command 0x0401, subcommand 0x0000)  : 워드 단위 연속 읽기
- Batch Write (command 0x1401, subcommand 0x0000)  : 워드 단위 연속 쓰기
지원 디바이스: D 레지스터(코드 0xA8) 만.

실제 미쓰비시 PLC(Q/L/iQ-R, FX5) 연결 시:
  * 디바이스 코드/헤드번호 표기(3바이트 vs 확장), 비트 디바이스 처리,
    프레임 종류(3E/4E), 포트(기본 UDP/TCP)만 장비에 맞게 조정하면
    아래 build_* / parse_* 골격을 그대로 사용할 수 있다.
"""
from __future__ import annotations

import struct

SUBHEADER_REQ = 0x0050
SUBHEADER_RES = 0x00D0
NET_NO = 0x00
PC_NO = 0xFF
DEST_MODULE_IO = 0x03FF
DEST_STATION = 0x00
CPU_TIMER = 0x0010  # 250ms 단위 감시 타이머

DEVICE_D = 0xA8

CMD_READ = 0x0401
CMD_WRITE = 0x1401
SUBCMD_WORD = 0x0000


class SlmpError(Exception):
    def __init__(self, end_code: int):
        super().__init__(f"SLMP end code 0x{end_code:04X}")
        self.end_code = end_code


def build_read_request(head_device: int, count: int) -> bytes:
    """D<head_device> 부터 count 워드 배치 리드 요청 프레임."""
    body = struct.pack("<HH", CMD_READ, SUBCMD_WORD)
    body += struct.pack("<I", head_device)[:3]      # 헤드번호 3바이트 LE
    body += bytes([DEVICE_D])
    body += struct.pack("<H", count)
    return _wrap(body)


def build_write_request(head_device: int, words: list[int]) -> bytes:
    """D<head_device> 부터 words 를 배치 라이트 요청 프레임."""
    body = struct.pack("<HH", CMD_WRITE, SUBCMD_WORD)
    body += struct.pack("<I", head_device)[:3]
    body += bytes([DEVICE_D])
    body += struct.pack("<H", len(words))
    for w in words:
        body += struct.pack("<H", w & 0xFFFF)
    return _wrap(body)


def _wrap(body: bytes) -> bytes:
    header = struct.pack(
        "<HBBHBH",
        SUBHEADER_REQ, NET_NO, PC_NO, DEST_MODULE_IO, DEST_STATION,
        len(body) + 2,           # request data length = timer(2) + body
    )
    header += struct.pack("<H", CPU_TIMER)
    return header + body


def parse_response(buf: bytes) -> bytes:
    """응답 프레임 검증 후 데이터부(엔드코드 이후)를 반환."""
    if len(buf) < 11:
        raise SlmpError(0xFFFF)
    (subheader, _net, _pc, _io, _st, rlen) = struct.unpack("<HBBHBH", buf[:9])
    if subheader != SUBHEADER_RES:
        raise SlmpError(0xFFFE)
    end_code = struct.unpack("<H", buf[9:11])[0]
    if end_code != 0:
        raise SlmpError(end_code)
    data = buf[11:11 + (rlen - 2)]
    return data


def decode_words(data: bytes) -> list[int]:
    return [struct.unpack("<H", data[i:i + 2])[0] for i in range(0, len(data) - 1, 2)]


def response_frame(words: list[int] | None, end_code: int = 0) -> bytes:
    """(시뮬레이터용) 응답 프레임 생성."""
    payload = struct.pack("<H", end_code)
    if words:
        for w in words:
            payload += struct.pack("<H", w & 0xFFFF)
    header = struct.pack(
        "<HBBHBH",
        SUBHEADER_RES, NET_NO, PC_NO, DEST_MODULE_IO, DEST_STATION,
        len(payload),
    )
    return header + payload


def parse_request(buf: bytes) -> dict:
    """(시뮬레이터용) 요청 프레임 해석."""
    (subheader, _net, _pc, _io, _st, _rlen) = struct.unpack("<HBBHBH", buf[:9])
    if subheader != SUBHEADER_REQ:
        raise SlmpError(0xC059)
    body = buf[11:]
    cmd, sub = struct.unpack("<HH", body[:4])
    head = struct.unpack("<I", body[4:7] + b"\x00")[0]
    dev = body[7]
    count = struct.unpack("<H", body[8:10])[0]
    out = {"cmd": cmd, "sub": sub, "head": head, "device": dev, "count": count}
    if cmd == CMD_WRITE:
        out["words"] = [
            struct.unpack("<H", body[10 + i:12 + i])[0] for i in range(0, count * 2, 2)
        ]
    return out
