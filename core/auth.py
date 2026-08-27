"""인증/권한. 세션 로그인 + 역할 3단계 + (선택) TOTP 2FA.

역할: viewer < operator < admin
- viewer  : 모니터링만
- operator: 기동/정지 명령(관리자 승인 필요할 수 있음)
- admin   : 전체 + 압력설정 + 명령승인 + 사용자관리
"""
from __future__ import annotations

import functools
import time

from flask import redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from core.db import audit, conn

try:  # 2FA 는 선택 의존성
    import pyotp
except ImportError:  # pragma: no cover
    pyotp = None

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def get_user(username: str):
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def list_users() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, username, role, created_at, must_change_pw, "
            "(totp_secret IS NOT NULL) AS has_2fa FROM users ORDER BY id")]


def verify_password(username: str, password: str):
    u = get_user(username)
    if u and check_password_hash(u["pw_hash"], password):
        return u
    return None


def totp_required(username: str) -> bool:
    u = get_user(username)
    return bool(u and u["totp_secret"])


def verify_totp(username: str, code: str) -> bool:
    u = get_user(username)
    if not u or not u["totp_secret"]:
        return True
    if pyotp is None:
        return False
    return pyotp.TOTP(u["totp_secret"]).verify(code, valid_window=1)


def change_password(username: str, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    with conn() as c:
        c.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE username=?",
                  (generate_password_hash(new_password), username))
    audit(username, "auth.change_password", "")


def create_user(username: str, password: str, role: str, actor: str) -> None:
    if role not in ROLE_RANK:
        raise ValueError("역할이 올바르지 않습니다.")
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    with conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("이미 존재하는 사용자입니다.")
        c.execute(
            "INSERT INTO users (username, pw_hash, role, created_at, must_change_pw) "
            "VALUES (?,?,?,?,1)",
            (username, generate_password_hash(password), role, time.time()),
        )
    audit(actor, "auth.create_user", f"{username} ({role})")


def set_role(username: str, role: str, actor: str) -> None:
    if role not in ROLE_RANK:
        raise ValueError("역할이 올바르지 않습니다.")
    with conn() as c:
        c.execute("UPDATE users SET role=? WHERE username=?", (role, username))
    audit(actor, "auth.set_role", f"{username} -> {role}")


def delete_user(username: str, actor: str) -> None:
    if username == actor:
        raise ValueError("자기 계정은 삭제할 수 없습니다.")
    with conn() as c:
        c.execute("DELETE FROM users WHERE username=?", (username,))
    audit(actor, "auth.delete_user", username)


# --- Flask 헬퍼 -------------------------------------------------------
def current_user() -> dict | None:
    return session.get("user")


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def role_required(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("login", next=request.path))
            if u["role"] not in roles and u["role"] != "admin":
                return {"error": "권한이 없습니다."}, 403
            return fn(*a, **kw)
        return wrapper
    return deco


def at_least(role: str) -> bool:
    u = current_user()
    return bool(u and ROLE_RANK.get(u["role"], 0) >= ROLE_RANK[role])
