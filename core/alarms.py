"""알람 엔진 + 알림(푸시/SMS 스텁).

poller 가 매 수집주기마다 evaluate() 를 호출한다.
- FAULT       : PLC FAULT_ALARM 비트 상승/해제 에지
- HIGH_TEMP   : 토출온도 임계 초과
- HIGH_CURRENT: 모터전류 임계 초과
- COMM_LOSS   : 통신 두절/복구
- LEAK_SUSPECT: 야간/휴일 무부하 압력강하 패턴 (스펙 요구사항)
"""
from __future__ import annotations

import datetime as dt
import time

from core.config import load
from core.db import conn

_last_leak_check: dict[str, float] = {}


def _get_open(c, unit: str, atype: str):
    return c.execute(
        "SELECT * FROM alarms WHERE unit=? AND type=? AND cleared_ts IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (unit, atype),
    ).fetchone()


def _raise(c, unit: str, atype: str, severity: str, message: str, value=None):
    if _get_open(c, unit, atype):
        return None
    cur = c.execute(
        "INSERT INTO alarms (unit, type, severity, message, value, raised_ts) "
        "VALUES (?,?,?,?,?,?)",
        (unit, atype, severity, message, value, time.time()),
    )
    aid = cur.lastrowid
    _notify(c, unit, atype, severity, message, aid)
    print(f"[alarm] {unit} {atype} ({severity}) {message}")
    return aid


def _clear(c, unit: str, atype: str):
    row = _get_open(c, unit, atype)
    if row:
        c.execute("UPDATE alarms SET cleared_ts=? WHERE id=?", (time.time(), row["id"]))
        print(f"[alarm] {unit} {atype} 해제")


def _notify(c, unit: str, atype: str, severity: str, message: str, alarm_id: int):
    cfg = load()["notifications"]
    subject = f"[{unit}] {atype}"
    body = f"{message} (severity={severity})"
    if cfg.get("push_enabled"):
        c.execute(
            "INSERT INTO notifications (ts, channel, target, subject, body, alarm_id, status)"
            " VALUES (?,?,?,?,?,?, 'SENT')",
            (time.time(), "push", "webapp", subject, body, alarm_id),
        )
    if cfg.get("sms_enabled") and severity in ("warning", "critical"):
        for rcpt in cfg.get("sms_recipients", []):
            c.execute(
                "INSERT INTO notifications (ts, channel, target, subject, body, alarm_id, status)"
                " VALUES (?,?,?,?,?,?, 'QUEUED')",
                (time.time(), "sms", rcpt, subject, body, alarm_id),
            )
            # 실제 연동 지점: 여기서 SMS 게이트웨이 API 호출 후 status 갱신.
            print(f"[sms→{rcpt}] {subject} {body}  (스텁: 실제 발송 미연동)")


def evaluate(unit: str, reading: dict) -> None:
    """reading: run_status, discharge_pressure(MPa), motor_current(A),
    fault_alarm(0/1), discharge_temp(C), online(bool)."""
    thr = load()["thresholds"]
    with conn() as c:
        if not reading.get("online", True):
            _raise(c, unit, "COMM_LOSS", "critical", "PLC 통신 두절")
            return
        _clear(c, unit, "COMM_LOSS")

        if reading.get("fault_alarm"):
            _raise(c, unit, "FAULT", "critical", "PLC 고장 알람(FAULT_ALARM)")
        else:
            _clear(c, unit, "FAULT")

        t = reading.get("discharge_temp")
        if t is not None and t >= thr["high_discharge_temp_c"]:
            _raise(c, unit, "HIGH_TEMP", "warning",
                   f"토출온도 {t:.1f}℃ ≥ {thr['high_discharge_temp_c']}℃", t)
        elif t is not None and t < thr["high_discharge_temp_c"] - 3:
            _clear(c, unit, "HIGH_TEMP")

        a = reading.get("motor_current")
        if a is not None and a >= thr["high_motor_current_a"]:
            _raise(c, unit, "HIGH_CURRENT", "warning",
                   f"모터전류 {a:.1f}A ≥ {thr['high_motor_current_a']}A", a)
        elif a is not None and a < thr["high_motor_current_a"] - 5:
            _clear(c, unit, "HIGH_CURRENT")

    _check_leak(unit)


def _in_quiet_window(now: dt.datetime, cfg: dict) -> bool:
    if cfg.get("include_weekends") and now.weekday() >= 5:
        return True
    h = now.hour
    s, e = cfg["night_start_hour"], cfg["night_end_hour"]
    return h >= s or h < e if s > e else s <= h < e


def _check_leak(unit: str) -> None:
    cfg = load()["leak_detection"]
    now = dt.datetime.now()
    if not _in_quiet_window(now, cfg):
        return
    # 유닛당 10분에 한 번만 평가
    if time.time() - _last_leak_check.get(unit, 0) < 600:
        return
    _last_leak_check[unit] = time.time()

    win = cfg["window_minutes"] * 60
    idle = cfg["min_idle_minutes"] * 60
    t0 = time.time() - win
    with conn() as c:
        rows = c.execute(
            "SELECT ts, run_status, discharge_pressure FROM readings "
            "WHERE unit=? AND ts>=? ORDER BY ts ASC",
            (unit, time.time() - max(win, idle)),
        ).fetchall()
        if len(rows) < 5:
            return
        # 관찰구간 내내 정지 상태였는가
        recent = [r for r in rows if r["ts"] >= time.time() - idle]
        if not recent or any(r["run_status"] for r in recent):
            _clear(c, unit, "LEAK_SUSPECT")
            return
        seg = [r for r in rows if r["ts"] >= t0 and r["discharge_pressure"] is not None]
        if len(seg) < 5:
            return
        drop = seg[0]["discharge_pressure"] - seg[-1]["discharge_pressure"]
        if drop >= cfg["drop_mpa"]:
            _raise(c, unit, "LEAK_SUSPECT", "warning",
                   f"야간/휴일 무부하 압력강하 {drop*1000:.0f}kPa/"
                   f"{cfg['window_minutes']}분 → 누설 의심", drop)
        elif drop < cfg["drop_mpa"] * 0.5:
            _clear(c, unit, "LEAK_SUSPECT")
