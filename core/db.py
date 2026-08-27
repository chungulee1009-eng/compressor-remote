"""SQLite 스키마 및 헬퍼.

연결은 호출 시마다 새로 열고 컨텍스트 매니저로 닫는다(스레드 안전).
WAL 모드로 읽기/쓰기 동시성 확보.
"""
from __future__ import annotations

import os
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
    """기본 계정 3개(admin/operator/viewer)를 보장한다.

    - 없는 계정은 기본 비밀번호로 생성.
    - 이미 있는 계정은 건드리지 않음(사용자 변경 존중).
    - COMPRESSOR_RESET_USERS=1 이면 3개 계정 비밀번호를 기본값으로 강제 초기화
      (클라우드처럼 디스크가 임시라 매번 알려진 계정이 필요할 때 사용).
    """
    defaults = [
        ("admin", "admin1234", "admin"),
        ("operator", "operator1234", "operator"),
        ("viewer", "viewer1234", "viewer"),
    ]
    force_reset = os.environ.get("COMPRESSOR_RESET_USERS") == "1"
    touched = []
    with conn() as c:
        existing = {r[0] for r in c.execute("SELECT username FROM users")}
        for username, pw, role in defaults:
            if username not in existing:
                c.execute(
                    "INSERT INTO users (username, pw_hash, role, created_at, must_change_pw) "
                    "VALUES (?,?,?,?,?)",
                    (username, generate_password_hash(pw), role, time.time(),
                     0 if force_reset else 1),
                )
                touched.append(username + "(생성)")
            elif force_reset:
                c.execute(
                    "UPDATE users SET pw_hash=?, role=?, must_change_pw=0 WHERE username=?",
                    (generate_password_hash(pw), role, username),
                )
                touched.append(username + "(초기화)")
    if touched:
        print("[db] 기본 계정 " + ", ".join(touched)
              + " : 비번 <계정>1234 (로그인 후 변경 요망)")


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
