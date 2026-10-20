"""임가공 발주서(수삽) 자동 작성 웹화면.

실행: python -m subcon_po.app   →  http://127.0.0.1:8071
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, render_template, request, send_file

from . import engine, rules
from .report import build_report
from .writer import available_backends, write_order

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"            # 마지막 발주서(기준 양식) 보관
JOBS = Path(tempfile.gettempdir()) / "subcon_po_jobs"
ALLOWED = {".xlsx", ".xlsm", ".xlsb"}

app = Flask(__name__, template_folder=str(BASE / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


def _last_order() -> Path | None:
    files = sorted(DATA.glob("기준발주서*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _save_upload(f, dst_dir: Path, stem: str) -> Path | None:
    if not f or not f.filename:
        return None
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"지원하지 않는 형식: {f.filename} (xlsx/xlsm/xlsb)")
    p = dst_dir / f"{stem}{ext}"
    f.save(p)
    return p


def _cleanup_jobs(hours: int = 24) -> None:
    if not JOBS.exists():
        return
    for d in JOBS.iterdir():
        if d.is_dir() and time.time() - d.stat().st_mtime > hours * 3600:
            shutil.rmtree(d, ignore_errors=True)


def _parse_holidays(text: str) -> list[date]:
    out = []
    for tok in text.replace("\n", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if tok:
            out.append(datetime.strptime(tok, "%Y-%m-%d").date())
    return out


def _index(error: str | None = None, status: int = 200):
    last = _last_order()
    return render_template("index.html", error=error, last_order=last.name if last else None,
                           defaults={"pull": rules.PULL_UP_QTY, "note": rules.UNREGISTERED_NOTE},
                           rules=rules), status


@app.get("/")
def index():
    return _index()


@app.post("/run")
def run():
    _cleanup_jobs()
    job = uuid.uuid4().hex[:12]
    jdir = JOBS / job
    jdir.mkdir(parents=True, exist_ok=True)
    try:
        plan = _save_upload(request.files.get("plan"), jdir, "plan")
        if not plan:
            raise ValueError("생산계획서를 업로드하세요.")
        order = _save_upload(request.files.get("order"), jdir, "order")
        if order:
            DATA.mkdir(exist_ok=True)
            for old in DATA.glob("기준발주서*"):
                old.unlink()
            shutil.copy2(order, DATA / f"기준발주서{order.suffix}")
        else:
            last = _last_order()
            if not last:
                raise ValueError("임가공 발주서를 업로드하세요. (최초 1회 필수, 이후 생략 시 마지막 발주서 사용)")
            order = jdir / f"order{last.suffix}"
            shutil.copy2(last, order)

        y = request.form.get("year", type=int)
        m = request.form.get("month", type=int)
        res = engine.build(plan, order, year=y, month=m,
                           pull=request.form.get("pull", rules.PULL_UP_QTY, type=int),
                           extra_holidays=_parse_holidays(request.form.get("holidays", "")),
                           unregistered_note=(request.form.get("note") or rules.UNREGISTERED_NOTE).strip())
        stem = f"{res.year}년 {res.month}월 임가공발주서_수삽갱신_{datetime.now():%y%m%d_%H%M}"
        build_report(res, jdir / "납품리스트.xlsx")
        start, end = res.order_section
        out_file, backend, write_err = None, None, None
        try:
            out_file, backend = write_order(order, jdir, start, end, engine.output_rows(res), stem)
        except Exception as e:
            write_err = str(e)
        return render_template("result.html", res=res, job=job, out_file=out_file.name if out_file else None,
                               backend=backend, write_err=write_err, report_name=f"{stem}_납품리스트.xlsx",
                               total=sum(d.qty for d in res.deliveries))
    except Exception as e:
        shutil.rmtree(jdir, ignore_errors=True)
        return _index(str(e), 400)


@app.get("/download/<job>/<kind>")
def download(job: str, kind: str):
    if not job.isalnum():
        abort(404)
    jdir = JOBS / job
    name = request.args.get("name", "")
    if kind == "report":
        p = jdir / "납품리스트.xlsx"
    elif kind == "order":
        p = jdir / Path(name).name
        if not name or p.name.startswith(("plan", "order")) or p.suffix.lower() not in ALLOWED:
            abort(404)
    else:
        abort(404)
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True, download_name=name or p.name)


def main():
    host = os.environ.get("SUBCON_HOST", "127.0.0.1")
    port = int(os.environ.get("SUBCON_PORT", "8071"))
    print(f" 임가공 발주서 작성기: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    print(f" 저장 방식: {available_backends('.xlsb') or ['(xlsb 저장 불가 — 납품리스트만 제공)']}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
