"""수집 게이트웨이: 9대 PLC 를 주기적으로 폴링 → SQLite 저장 → 알람 판정 →
승인된 제어명령 실행.
"""
from __future__ import annotations

import threading
import time

from core import alarms, control
from core.config import load
from core.db import conn, prune_old_readings
from gateway.client import SlmpClient


def decode(words: list[int]) -> dict:
    cfg = load()
    d = cfg["devices"]
    sc = cfg["scaling"]
    base = d["read_head"]

    def w(addr):
        return words[addr - base]

    rh = w(d["run_hours_low"]) | (w(d["run_hours_high"]) << 16)
    return {
        "run_status": int(w(d["run_status"])),
        "discharge_pressure": w(d["discharge_pressure"]) / sc["pressure_div"],
        "motor_current": w(d["motor_current"]) / sc["current_div"],
        "fault_alarm": int(w(d["fault_alarm"])),
        "discharge_temp": w(d["discharge_temp"]) / sc["temp_div"],
        "run_hours_accum": rh,
        "online": True,
    }


class Poller:
    def __init__(self):
        cfg = load()
        self.interval = cfg["polling"]["interval_sec"]
        self.devices = cfg["devices"]
        self.clients: dict[str, SlmpClient] = {}
        for u in cfg["units"]:
            self.clients[u["id"]] = SlmpClient(u["host"], u["port"])
        self._stop = threading.Event()
        self._prune_at = 0.0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"[gw] 게이트웨이 폴링 시작 (주기 {self.interval}s, {len(self.clients)}대)")

    def stop(self):
        self._stop.set()
        for c in self.clients.values():
            c.close()

    def _poll_once(self, uid: str) -> dict:
        cli = self.clients[uid]
        try:
            words = cli.read_words(self.devices["read_head"], self.devices["read_count"])
            r = decode(words)
        except Exception as exc:  # noqa: BLE001
            cli.close()
            r = {
                "run_status": None, "discharge_pressure": None, "motor_current": None,
                "fault_alarm": None, "discharge_temp": None, "run_hours_accum": None,
                "online": False, "_err": str(exc),
            }
        self._store(uid, r)
        try:
            alarms.evaluate(uid, r)
        except Exception as exc:  # noqa: BLE001
            print(f"[gw] {uid} 알람평가 오류: {exc}")
        return r

    @staticmethod
    def _store(uid: str, r: dict):
        with conn() as c:
            c.execute(
                "INSERT INTO readings (unit, ts, run_status, discharge_pressure, "
                "motor_current, fault_alarm, discharge_temp, run_hours_accum, online) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, time.time(), r["run_status"], r["discharge_pressure"],
                 r["motor_current"], r["fault_alarm"], r["discharge_temp"],
                 r["run_hours_accum"], 1 if r["online"] else 0),
            )

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            for uid in self.clients:
                if self._stop.is_set():
                    break
                self._poll_once(uid)
            try:
                control.apply_approved(self.clients)
            except Exception as exc:  # noqa: BLE001
                print(f"[gw] 제어명령 적용 오류: {exc}")

            if time.time() - self._prune_at > 3600:
                self._prune_at = time.time()
                n = prune_old_readings()
                if n:
                    print(f"[gw] 오래된 수집데이터 {n}건 정리")

            elapsed = time.time() - t0
            self._stop.wait(max(0.2, self.interval - elapsed))


if __name__ == "__main__":
    from core.config import load as _l
    from core.db import init_db
    _l()
    init_db()
    p = Poller()
    p.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        p.stop()
