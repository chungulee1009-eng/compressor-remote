"""가상 컴프레셔 PLC 시뮬레이터.

유닛마다 D 레지스터 파일 + 간단한 물리모델을 갖고,
mini-SLMP(gateway.protocol) TCP 서버로 배치 리드/라이트를 처리한다.
게이트웨이(poller) 코드는 실제 PLC 든 이 시뮬레이터 든 동일하게 동작한다.
"""
from __future__ import annotations

import math
import random
import socket
import socketserver
import struct
import threading
import time

from core.config import load
from gateway import protocol as slmp

RATED_CURRENT_A = 128.0        # 100HP / 74.6kW, 380V 3상 환산 정격전류 근사
AMBIENT_C = 26.0

# 데모용 초기 상태 및 누설계수(야간 무부하 압력강하 감지 시연을 위해 일부 유닛만 크게).
_INIT = {
    "COMP-01": dict(state="RUNNING", leak=3.0e-6),
    "COMP-02": dict(state="RUNNING", leak=2.5e-6),
    "COMP-03": dict(state="RUNNING", leak=6.5e-5),   # 누설 큰 유닛
    "COMP-04": dict(state="RUNNING", leak=3.2e-6),
    "COMP-05": dict(state="STOPPED", leak=2.0e-6),
    "COMP-06": dict(state="RUNNING", leak=2.8e-6),
    "COMP-07": dict(state="STOPPED", leak=7.0e-5),   # 누설 큰 유닛(정지 중)
    "COMP-08": dict(state="RUNNING", leak=3.0e-6),
    "COMP-09": dict(state="STOPPED", leak=2.2e-6),
}


class Compressor:
    def __init__(self, uid: str):
        cfg = load()
        thr = cfg["thresholds"]
        init = _INIT.get(uid, dict(state="STOPPED", leak=3.0e-6))
        self.uid = uid
        self.lock = threading.Lock()
        self.state = init["state"]              # RUNNING / STOPPED / FAULT
        self.leak = init["leak"]               # MPa/s
        self.setpoint = thr["target_pressure_mpa"]
        self.band = thr["pressure_band_mpa"]
        self.pressure = self.setpoint if self.state == "RUNNING" else self.setpoint - 0.10
        self.loaded = self.state == "RUNNING"
        self.current = 0.0
        self.temp = AMBIENT_C
        self.run_seconds = random.randint(200, 9000) * 3600.0
        self._demand = 0.00025                  # MPa/s 상당 수요(부하 소비)
        self._fault_reason = ""

    # --- 물리모델 1틱 -------------------------------------------------
    def tick(self, dt: float):
        with self.lock:
            self._demand += random.uniform(-3e-5, 3e-5)
            self._demand = min(max(self._demand, 8e-5), 5e-4)

            if self.state == "FAULT":
                self.pressure -= self.leak * dt
                self.current = 0.0
                self._cool(dt)
            elif self.state == "STOPPED":
                # 전체 정지 시 배관 압력은 누설분 만큼만 하강
                self.pressure -= self.leak * dt
                self.current = 0.0
                self._cool(dt)
            else:  # RUNNING
                if self.pressure >= self.setpoint + self.band:
                    self.loaded = False
                elif self.pressure <= self.setpoint - self.band:
                    self.loaded = True
                rate_up = 0.0016 if self.loaded else 0.0
                self.pressure += (rate_up - self._demand - self.leak) * dt
                base = RATED_CURRENT_A * (random.uniform(0.88, 1.04) if self.loaded
                                          else random.uniform(0.30, 0.38))
                self.current += (base - self.current) * min(1.0, dt / 3.0)
                target_t = AMBIENT_C + (38 if self.loaded else 16) \
                    + 6 * math.tanh(self.run_seconds / 3.6e6)
                self.temp += (target_t - self.temp) * min(1.0, dt / 25.0)
                self.temp += random.uniform(-0.15, 0.15)
                self.run_seconds += dt

            self.pressure = max(0.0, self.pressure)

            # 안전: 과압 릴리프 -> FAULT
            if self.pressure > self.setpoint + 0.30 and self.state == "RUNNING":
                self.state = "FAULT"
                self._fault_reason = "OVER_PRESSURE"
            # 드문 랜덤 트립
            if self.state == "RUNNING" and random.random() < 1.2e-5 * dt:
                self.state = "FAULT"
                self._fault_reason = "MOTOR_TRIP"

    def _cool(self, dt: float):
        self.temp += (AMBIENT_C - self.temp) * min(1.0, dt / 120.0)

    # --- 레지스터 뷰 -----------------------------------------------
    def registers(self) -> dict[int, int]:
        cfg = load()
        d = cfg["devices"]
        sc = cfg["scaling"]
        with self.lock:
            run = 1 if self.state == "RUNNING" else 0
            fault = 1 if self.state == "FAULT" else 0
            rh = int(self.run_seconds // 3600)
            return {
                d["run_status"]: run,
                d["discharge_pressure"]: int(round(self.pressure * sc["pressure_div"])),
                d["motor_current"]: int(round(self.current * sc["current_div"])),
                d["fault_alarm"]: fault,
                d["discharge_temp"]: int(round(self.temp * sc["temp_div"])),
                d["run_hours_low"]: rh & 0xFFFF,
                d["run_hours_high"]: (rh >> 16) & 0xFFFF,
                d["pressure_setpoint"]: int(round(self.setpoint * sc["pressure_div"])),
            }

    def write_register(self, addr: int, value: int):
        cfg = load()
        d = cfg["devices"]
        sc = cfg["scaling"]
        with self.lock:
            if addr == d["cmd_start"] and value == 1:
                self.state = "RUNNING"
                self.loaded = True
                self._fault_reason = ""
            elif addr == d["cmd_stop"] and value == 1:
                self.state = "STOPPED"
                self.loaded = False
                self._fault_reason = ""
            elif addr == d["pressure_setpoint"] and value > 0:
                self.setpoint = value / sc["pressure_div"]


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock: socket.socket = self.request
        sock.settimeout(30)
        comp: Compressor = self.server.compressor          # type: ignore[attr-defined]
        try:
            while True:
                hdr = _recvn(sock, 9)
                if not hdr:
                    return
                rlen = struct.unpack("<H", hdr[7:9])[0]
                rest = _recvn(sock, rlen)
                if rest is None:
                    return
                try:
                    req = slmp.parse_request(hdr + rest)
                except Exception:
                    sock.sendall(slmp.response_frame(None, end_code=0xC059))
                    continue

                if req["cmd"] == slmp.CMD_READ:
                    regs = comp.registers()
                    words = [regs.get(req["head"] + i, 0) for i in range(req["count"])]
                    sock.sendall(slmp.response_frame(words, 0))
                elif req["cmd"] == slmp.CMD_WRITE:
                    for i, w in enumerate(req["words"]):
                        comp.write_register(req["head"] + i, w)
                    sock.sendall(slmp.response_frame(None, 0))
                else:
                    sock.sendall(slmp.response_frame(None, end_code=0xC060))
        except (socket.timeout, ConnectionError, OSError):
            return


def _recvn(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            return None
        if not chunk:
            return None if buf else b""
        buf += chunk
    return buf


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Simulator:
    """모든 유닛의 물리모델 틱 + 유닛별 TCP 서버를 관리."""

    def __init__(self):
        cfg = load()
        self.tick_sec = cfg["simulator"]["tick_sec"]
        self.bind_host = cfg["simulator"]["bind_host"]
        self.compressors: dict[str, Compressor] = {}
        self.servers: list[_Server] = []
        self._stop = threading.Event()
        for u in cfg["units"]:
            if not u.get("sim", True):
                # 실제 PLC 로 연결하는 유닛 - 가상 서버를 만들지 않는다.
                continue
            c = Compressor(u["id"])
            self.compressors[u["id"]] = c
            srv = _Server((self.bind_host, u["port"]), _Handler)
            srv.compressor = c                     # type: ignore[attr-defined]
            self.servers.append(srv)

    def start(self):
        if not self.servers:
            print("[sim] 가상 PLC 없음 (모든 유닛이 실제 PLC 연결)")
            return
        for srv in self.servers:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        ports = ", ".join(str(s.server_address[1]) for s in self.servers)
        print(f"[sim] 가상 PLC {len(self.servers)}대 기동 (mini-SLMP TCP {ports})")

    def _loop(self):
        last = time.time()
        while not self._stop.is_set():
            time.sleep(self.tick_sec)
            now = time.time()
            dt = now - last
            last = now
            for c in self.compressors.values():
                c.tick(dt)

    def stop(self):
        self._stop.set()
        for srv in self.servers:
            srv.shutdown()


if __name__ == "__main__":
    from core.config import load as _l
    _l()
    sim = Simulator()
    sim.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()
