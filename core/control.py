"""원격 제어 명령 큐 + 관리자 승인 워크플로.

스펙: 개별 기동/정지(권한별 승인 필요), 압력 설정값 변경(관리자만).
- viewer  : 제어 불가(웹 계층에서 차단)
- operator: 명령 생성 → require_admin_approval_for_operator=true 면 PENDING
- admin   : 즉시 APPROVED (승인 겸함)
poller 가 APPROVED 명령을 PLC 에 기록하고 EXECUTED 로 마감한다.
"""
from __future__ import annotations

import time

from core.config import load, unit_by_id
from core.db import audit, conn

VALID_ACTIONS = ("start", "stop", "set_pressure")


class ControlError(Exception):
    pass


def control_enabled(unit: str) -> bool:
    """유닛 config 에 control: false 면 모니터링 전용."""
    u = unit_by_id(unit)
    return bool(u) and u.get("control", True) is not False


def submit(unit: str, action: str, value, username: str, role: str) -> dict:
    if action not in VALID_ACTIONS:
        raise ControlError(f"알 수 없는 명령: {action}")
    if not control_enabled(unit):
        raise ControlError("이 설비는 모니터링 전용입니다 (config 에서 control 비활성).")
    if role == "viewer":
        raise ControlError("viewer 권한은 제어할 수 없습니다.")
    if action == "set_pressure":
        if role != "admin":
            raise ControlError("압력 설정값 변경은 admin 권한만 가능합니다.")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ControlError("압력 값이 올바르지 않습니다.")
        thr = load()["thresholds"]
        lo, hi = 0.2, thr["target_pressure_mpa"] + 0.5
        if not (lo <= value <= hi):
            raise ControlError(f"압력 설정 범위를 벗어났습니다 ({lo}~{hi} MPa).")
    else:
        value = None

    need_approval = (
        role == "operator"
        and load()["control"].get("require_admin_approval_for_operator", True)
    )
    status = "PENDING" if need_approval else "APPROVED"
    now = time.time()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO commands (unit, action, value, requested_by, requested_ts, "
            "status, approved_by, approved_ts) VALUES (?,?,?,?,?,?,?,?)",
            (unit, action, value, username, now, status,
             username if status == "APPROVED" else None,
             now if status == "APPROVED" else None),
        )
        cid = cur.lastrowid
    audit(username, "command.submit",
          f"#{cid} {unit} {action} {value if value is not None else ''} -> {status}")
    return {"id": cid, "status": status}


def approve(cid: int, admin_user: str) -> None:
    with conn() as c:
        row = c.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()
        if not row or row["status"] != "PENDING":
            raise ControlError("승인 대기 상태의 명령이 아닙니다.")
        c.execute(
            "UPDATE commands SET status='APPROVED', approved_by=?, approved_ts=? WHERE id=?",
            (admin_user, time.time(), cid),
        )
    audit(admin_user, "command.approve", f"#{cid} {row['unit']} {row['action']}")


def reject(cid: int, admin_user: str, note: str = "") -> None:
    with conn() as c:
        row = c.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()
        if not row or row["status"] != "PENDING":
            raise ControlError("승인 대기 상태의 명령이 아닙니다.")
        c.execute(
            "UPDATE commands SET status='REJECTED', approved_by=?, approved_ts=?, note=? WHERE id=?",
            (admin_user, time.time(), note, cid),
        )
    audit(admin_user, "command.reject", f"#{cid} {note}")


def pending() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM commands WHERE status='PENDING' ORDER BY requested_ts ASC"
        )]


def recent(limit: int = 50) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,)
        )]


def apply_approved(clients: dict) -> None:
    """poller 전용: APPROVED 명령을 PLC 레지스터에 기록."""
    cfg = load()
    d = cfg["devices"]
    sc = cfg["scaling"]
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM commands WHERE status='APPROVED' ORDER BY id ASC"
        )]
    for row in rows:
        cli = clients.get(row["unit"])
        if cli is None:
            continue
        try:
            if row["action"] == "start":
                cli.write_words(d["cmd_start"], [1])
            elif row["action"] == "stop":
                cli.write_words(d["cmd_stop"], [1])
            elif row["action"] == "set_pressure":
                cli.write_words(d["pressure_setpoint"],
                                [int(round(row["value"] * sc["pressure_div"]))])
            with conn() as c:
                c.execute("UPDATE commands SET status='EXECUTED', executed_ts=? WHERE id=?",
                          (time.time(), row["id"]))
            audit("gateway", "command.execute",
                  f"#{row['id']} {row['unit']} {row['action']}")
            print(f"[control] #{row['id']} {row['unit']} {row['action']} 실행 완료")
        except Exception as exc:  # noqa: BLE001
            with conn() as c:
                c.execute("UPDATE commands SET status='FAILED', note=? WHERE id=?",
                          (str(exc), row["id"]))
            audit("gateway", "command.fail", f"#{row['id']} {exc}")
            print(f"[control] #{row['id']} 실행 실패: {exc}")
