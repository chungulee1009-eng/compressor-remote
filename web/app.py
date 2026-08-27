"""Flask 웹앱: 모바일 대응 대시보드(PWA) + REST API."""
from __future__ import annotations

import io
import time

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from core import auth, control
from core.config import load, unit_by_id, unit_ids
from core.db import audit, conn

app = Flask(__name__, template_folder="templates", static_folder="static")
_cfg = load()
app.secret_key = _cfg["server"]["secret_key"]
app.config["JSON_AS_ASCII"] = False


# ---------------------------------------------------------------- 데이터 헬퍼
def latest_reading(uid: str):
    with conn() as c:
        return c.execute(
            "SELECT * FROM readings WHERE unit=? ORDER BY ts DESC LIMIT 1", (uid,)
        ).fetchone()


def open_alarms(uid: str | None = None):
    with conn() as c:
        if uid:
            rows = c.execute(
                "SELECT * FROM alarms WHERE cleared_ts IS NULL AND unit=? ORDER BY raised_ts DESC",
                (uid,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM alarms WHERE cleared_ts IS NULL ORDER BY raised_ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def overview() -> list[dict]:
    out = []
    opens = open_alarms()
    by_unit: dict[str, list] = {}
    for a in opens:
        by_unit.setdefault(a["unit"], []).append(a)
    for uid in unit_ids():
        r = latest_reading(uid)
        ua = by_unit.get(uid, [])
        stale = (not r) or (time.time() - r["ts"] > 15)
        out.append({
            "unit": uid,
            "ts": r["ts"] if r else None,
            "online": bool(r and r["online"]) and not stale,
            "run_status": r["run_status"] if r else None,
            "discharge_pressure": r["discharge_pressure"] if r else None,
            "motor_current": r["motor_current"] if r else None,
            "discharge_temp": r["discharge_temp"] if r else None,
            "fault_alarm": r["fault_alarm"] if r else None,
            "run_hours_accum": r["run_hours_accum"] if r else None,
            "open_alarms": len(ua),
            "worst_severity": _worst([a["severity"] for a in ua]),
        })
    return out


def _worst(sevs: list[str]) -> str | None:
    for s in ("critical", "warning", "info"):
        if s in sevs:
            return s
    return None


# ---------------------------------------------------------------- 인증
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        code = request.form.get("code", "").strip()
        u = auth.verify_password(username, password)
        if not u:
            return render_template("login.html", error="아이디 또는 비밀번호가 올바르지 않습니다.",
                                   site=_cfg["site"]), 401
        if auth.totp_required(username) and not auth.verify_totp(username, code):
            return render_template("login.html", error="2FA 인증코드가 올바르지 않습니다.",
                                   username=username, need_2fa=True, site=_cfg["site"]), 401
        session["user"] = {"username": u["username"], "role": u["role"]}
        audit(u["username"], "auth.login", request.remote_addr or "")
        if u["must_change_pw"]:
            return redirect(url_for("account", first=1))
        return redirect(request.args.get("next") or url_for("dashboard"))
    return render_template("login.html", site=_cfg["site"])


@app.route("/logout")
def logout():
    u = auth.current_user()
    if u:
        audit(u["username"], "auth.logout", "")
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- 페이지
@app.route("/")
@auth.login_required
def dashboard():
    return render_template("dashboard.html", site=_cfg["site"], user=auth.current_user())


@app.route("/unit/<uid>")
@auth.login_required
def unit_page(uid):
    if uid not in unit_ids():
        return "존재하지 않는 유닛", 404
    u_cfg = unit_by_id(uid) or {}
    ctrl_ok = u_cfg.get("control", True) is not False
    return render_template("unit.html", site=_cfg["site"], user=auth.current_user(),
                           uid=uid,
                           can_control=auth.at_least("operator") and ctrl_ok,
                           can_setpoint=auth.at_least("admin") and ctrl_ok,
                           monitor_only=not ctrl_ok,
                           thresholds=_cfg["thresholds"])


@app.route("/alarms")
@auth.login_required
def alarms_page():
    return render_template("alarms.html", site=_cfg["site"], user=auth.current_user())


@app.route("/admin")
@auth.login_required
def admin_page():
    if not auth.at_least("admin"):
        return "권한이 없습니다.", 403
    return render_template("admin.html", site=_cfg["site"], user=auth.current_user(),
                           users=auth.list_users())


@app.route("/account", methods=["GET", "POST"])
@auth.login_required
def account():
    u = auth.current_user()
    if request.method == "POST":
        try:
            auth.change_password(u["username"], request.form.get("new_password", ""))
        except ValueError as e:
            return render_template("account.html", site=_cfg["site"], user=u, error=str(e))
        return render_template("account.html", site=_cfg["site"], user=u,
                               ok="비밀번호가 변경되었습니다.")
    return render_template("account.html", site=_cfg["site"], user=u,
                           first=request.args.get("first"))


# ---------------------------------------------------------------- API
@app.get("/api/overview")
@auth.login_required
def api_overview():
    units_ov = overview()
    eq = _cfg.get("equipment", {}) or {}
    hp = eq.get("rated_power_hp", 100)
    kw = eq.get("rated_power_kw", 74.6)
    n_run = sum(1 for u in units_ov if u["run_status"])
    n_all = len(units_ov)
    return jsonify({
        "ts": time.time(),
        "units": units_ov,
        "totals": {
            "unit_count": n_all,
            "running_count": n_run,
            "rated_hp_per_unit": hp,
            "rated_kw_per_unit": kw,
            "running_hp": n_run * hp,
            "total_hp": n_all * hp,
            "running_kw": round(n_run * kw, 1),
            "total_kw": round(n_all * kw, 1),
        },
    })


@app.get("/api/units/<uid>/latest")
@auth.login_required
def api_latest(uid):
    r = latest_reading(uid)
    return jsonify(dict(r) if r else {})


@app.get("/api/units/<uid>/history")
@auth.login_required
def api_history(uid):
    rng = request.args.get("range", "24h")
    span, bucket = {"1h": (3600, 15), "24h": (86400, 60),
                    "7d": (7 * 86400, 600)}.get(rng, (86400, 60))
    t0 = time.time() - span
    with conn() as c:
        rows = c.execute(
            f"SELECT CAST(ts/{bucket} AS INT)*{bucket} AS b, "
            "AVG(discharge_pressure) p, AVG(motor_current) a, AVG(discharge_temp) t, "
            "MAX(run_status) run, MAX(fault_alarm) fault "
            "FROM readings WHERE unit=? AND ts>=? GROUP BY b ORDER BY b",
            (uid, t0)).fetchall()
    return jsonify({
        "unit": uid, "range": rng,
        "series": [
            {"t": r["b"], "pressure": r["p"], "current": r["a"], "temp": r["t"],
             "run": r["run"], "fault": r["fault"]}
            for r in rows
        ],
    })


@app.get("/api/alarms")
@auth.login_required
def api_alarms():
    state = request.args.get("state", "open")
    with conn() as c:
        if state == "open":
            rows = c.execute(
                "SELECT * FROM alarms WHERE cleared_ts IS NULL ORDER BY raised_ts DESC"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM alarms ORDER BY raised_ts DESC LIMIT 300").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/alarms/<int:aid>/ack")
@auth.login_required
def api_ack(aid):
    u = auth.current_user()
    with conn() as c:
        c.execute("UPDATE alarms SET acknowledged=1, ack_by=?, ack_ts=? WHERE id=?",
                  (u["username"], time.time(), aid))
    audit(u["username"], "alarm.ack", f"#{aid}")
    return jsonify({"ok": True})


@app.post("/api/units/<uid>/command")
@auth.login_required
def api_command(uid):
    u = auth.current_user()
    if uid not in unit_ids():
        return jsonify({"error": "존재하지 않는 유닛"}), 404
    body = request.get_json(silent=True) or {}
    try:
        res = control.submit(uid, body.get("action"), body.get("value"),
                             u["username"], u["role"])
    except control.ControlError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@app.get("/api/commands")
@auth.login_required
def api_commands():
    return jsonify(control.recent())


@app.get("/api/commands/pending")
@auth.login_required
def api_pending():
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    return jsonify(control.pending())


@app.post("/api/commands/<int:cid>/approve")
@auth.login_required
def api_approve(cid):
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    try:
        control.approve(cid, auth.current_user()["username"])
    except control.ControlError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/commands/<int:cid>/reject")
@auth.login_required
def api_reject(cid):
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    body = request.get_json(silent=True) or {}
    try:
        control.reject(cid, auth.current_user()["username"], body.get("note", ""))
    except control.ControlError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.get("/api/notifications/recent")
@auth.login_required
def api_notifications():
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(r) for r in rows])


# --- 사용자 관리(admin) ---
@app.post("/api/users")
@auth.login_required
def api_create_user():
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    b = request.get_json(silent=True) or {}
    try:
        auth.create_user(b.get("username", "").strip(), b.get("password", ""),
                         b.get("role", "viewer"), auth.current_user()["username"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/users/<username>/role")
@auth.login_required
def api_set_role(username):
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    b = request.get_json(silent=True) or {}
    try:
        auth.set_role(username, b.get("role", ""), auth.current_user()["username"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/users/<username>/delete")
@auth.login_required
def api_delete_user(username):
    if not auth.at_least("admin"):
        return jsonify({"error": "권한이 없습니다."}), 403
    try:
        auth.delete_user(username, auth.current_user()["username"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# --- PWA ---
@app.get("/manifest.webmanifest")
def manifest():
    return app.send_static_file("manifest.webmanifest")


@app.get("/sw.js")
def sw():
    resp = app.make_response(app.send_static_file("sw.js"))
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# --- QR 설치 안내 (로그인 불필요) ---
def _install_url() -> str:
    """QR 이 가리킬 주소. 서버 접속 경로에 맞춰 자동 결정
    (LAN 으로 열면 LAN 주소, ngrok/Tailscale 로 열면 그 주소)."""
    root = request.url_root
    if request.headers.get("X-Forwarded-Proto") == "https":
        root = root.replace("http://", "https://", 1)
    return root


@app.get("/join")
def join():
    url = _install_url()
    return render_template("join.html", site=_cfg["site"], url=url,
                           is_https=url.startswith("https://"))


@app.get("/qr.png")
def qr_png():
    import qrcode  # 지연 임포트(설치 안 돼 있어도 앱은 뜨도록)
    target = request.args.get("url") or _install_url()
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="join-qr.png")


@app.context_processor
def inject_globals():
    return {"unit_ids": unit_ids()}


def create_app():
    return app


if __name__ == "__main__":
    from core.db import init_db
    init_db()
    app.run(host=_cfg["server"]["web_host"], port=_cfg["server"]["web_port"], debug=True)
