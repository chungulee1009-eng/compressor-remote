"""SQLite 스키마 및 헬퍼.

연결은 호출 시마다 새로 열고 컨텍스트 매니저로 닫는다(스레드 안전).
WAL 모드로 읽기/쓰기 동시성 확보.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager

from werkzeug.security import generate_password_hash

from core.config import load

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    pw_hash       TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('viewer','operator','admin')),
    totp_secret   TEXT,
    created_at    REAL NOT NULL,
    must_change_pw INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit          TEXT NOT NULL,
    ts            REAL NOT NULL,
    run_status    INTEGER,
    discharge_pressure REAL,   -- MPa
    motor_current REAL,        -- A
    fault_alarm   INTEGER,
    discharge_temp REAL,       -- degC
    run_hours_accum INTEGER,   -- hr
    online        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_readings_unit_ts ON readings (unit, ts);

CREATE TABLE IF NOT EXISTS alarms (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit          TEXT NOT NULL,
    type          TEXT NOT NULL,      -- FAULT / HIGH_TEMP / HIGH_CURRENT / LEAK_SUSPECT / COMM_LOSS
    severity      TEXT NOT NULL,      -- info / warning / critical
    message       TEXT NOT NULL,
    value         REAL,
    raised_ts     REAL NOT NULL,
    cleared_ts    REAL,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    ack_by        TEXT,
    ack_ts        REAL
);
CREATE INDEX IF NOT EXISTS idx_alarms_open ON alarms (unit, type, cleared_ts);

CREATE TABLE IF NOT EXISTS commands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit          TEXT NOT NULL,
    action        TEXT NOT NULL,      -- start / stop / set_pressure
    value         REAL,
    requested_by  TEXT NOT NULL,
    requested_ts  REAL NOT NULL,
    status        TEXT NOT NULL,      -- PENDING / APPROVED / REJECTED / EXECUTED / FAILED
    approved_by   TEXT,
    approved_ts   REAL,
    executed_ts   REAL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands (status);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    channel   TEXT NOT NULL,   -- push / sms
    target    TEXT NOT NULL,
    subject   TEXT NOT NULL,
    body      TEXT NOT NULL,
    alarm_id  INTEGER,
    status    TEXT NOT NULL    -- QUEUED / SENT / FAILED
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(load()["database"]["abspath"], timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
    _seed_users()


def _seed_users() -> None:
    """최초 1회: 기본 계정 3개 생성. 운영 전 반드시 비밀번호 변경."""
    defaults = [
        ("admin", "admin1234", "admin"),
        ("operator", "operator1234", "operator"),
        ("viewer", "viewer1234", "viewer"),
    ]
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n:
            return
        for username, pw, role in defaults:
            c.execute(
                "INSERT INTO users (username, pw_hash, role, created_at, must_change_pw) "
                "VALUES (?,?,?,?,1)",
                (username, generate_password_hash(pw), role, time.time()),
            )
    print("[db] 기본 계정 생성: admin/admin1234, operator/operator1234, viewer/viewer1234 "
          "(로그인 후 즉시 변경 요망)")


def audit(actor: str, action: str, detail: str = "") -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
            (time.time(), actor, action, detail),
        )


def prune_old_readings() -> int:
    days = load()["polling"]["history_retention_days"]
    cutoff = time.time() - days * 86400
    with conn() as c:
        cur = c.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        return cur.rowcount
