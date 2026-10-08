"""Google 음성인식(Chrome 내장, Google 문서 '음성 입력'과 같은 엔진)으로 실시간 자막.

구조: 프로그램이 PC 안에 작은 웹페이지(127.0.0.1)를 띄우고 Chrome 창으로 연다.
     페이지가 Chrome 음성인식으로 받아쓴 글자를 즉시 프로그램으로 보낸다.
주의: 음성이 Google 서버로 전송된다(인터넷 필요). 대외비 회의는 'PC 내부' 엔진 사용.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

PORTS = range(8771, 8781)  # 고정 포트 → Chrome 마이크 허용이 다음 실행에도 유지됨

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>SAM4S 음성인식 (Google)</title>
<style>
 body{font-family:'Malgun Gothic',sans-serif;margin:0;padding:14px;background:#f4f6fa;color:#1f3864}
 h1{font-size:15px;margin:0 0 6px} #st{font-size:13px;color:#555;margin-bottom:8px}
 #live{font-size:17px;color:#9aa0a6;min-height:48px} #last{font-size:15px;color:#222;margin-top:6px}
 .on{color:#c00000!important;font-weight:bold} small{color:#888}
</style></head><body>
<h1>🎙 SAM4S AI 회의록 — Google 음성인식</h1>
<div id="st">프로그램과 연결 중...</div><div id="live"></div><div id="last"></div>
<p><small>이 창은 회의 중 열어 두세요 (최소화 가능). 받아쓴 글자는 회의록 프로그램에 바로 표시됩니다.</small></p>
<script>
const R = window.SpeechRecognition || window.webkitSpeechRecognition;
const st = document.getElementById('st'), live = document.getElementById('live'), last = document.getElementById('last');
let rec = null, want = false, running = false, sess = null, lastPartial = null, blocked = false;
function post(type, text) {
  fetch('/event', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({type, text, session: sess})}).catch(()=>{});
}
function setSt(t, on) { st.textContent = t; st.className = on ? 'on' : ''; }
function make() {
  rec = new R(); rec.lang = 'ko-KR'; rec.continuous = true; rec.interimResults = true; rec.maxAlternatives = 1;
  rec.onstart = () => { running = true; setSt('● 받아쓰는 중 (Google 음성인식)', true); post('status', 'listening'); };
  rec.onresult = (e) => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) { const t = r[0].transcript.trim(); if (t) { post('final', t); last.textContent = t; } lastPartial = ''; }
      else interim += r[0].transcript;
    }
    interim = interim.trim();
    if (interim !== lastPartial) { lastPartial = interim; post('partial', interim); }
    live.textContent = interim;
  };
  rec.onerror = (e) => {
    post('error', e.error);
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {  // 허용 전까지 재시도하지 않음
      want = false; blocked = true; setSt('마이크 사용이 차단됨 — 주소창 왼쪽 자물쇠 → 마이크 허용 후 다시 녹음', false); }
    else if (e.error === 'network') setSt('인터넷 연결 확인 필요 (Google 음성인식은 온라인 전용)', false);
  };
  rec.onend = () => { running = false; if (want) setTimeout(start, 250); else if (!blocked) setSt('대기 중 — 프로그램에서 녹음을 시작하세요', false); };
}
function start() { if (!want || running) return; try { if (!rec) make(); rec.start(); } catch (e) { setTimeout(start, 500); } }
async function poll() {
  try {
    const s = await (await fetch('/state', {cache:'no-store'})).json();
    if (s.active) {
      if (s.session !== sess) { sess = s.session; blocked = false; want = false; }  // 새 녹음 → 다시 시도
      if (!want && !blocked) { want = true; lastPartial = null; start(); }
    } else if (want) { want = false; if (rec) rec.stop(); }
    if (!s.active && !running && !blocked) setSt('대기 중 — 프로그램에서 녹음을 시작하세요', false);
  } catch (e) { setSt('프로그램과 연결이 끊김 — 회의록 프로그램을 확인하세요', false); }
}
if (!R) setSt('이 브라우저는 음성인식을 지원하지 않습니다. Chrome 또는 Edge 로 여세요.', false);
else { setInterval(poll, 300); poll(); }
</script></body></html>"""


def _browser_cmd(url: str) -> list[str] | None:
    """Chrome(없으면 Edge)을 작은 앱 창으로 여는 명령."""
    cands = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands.append(Path(base) / "Google/Chrome/Application/chrome.exe")
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            base = os.environ.get(env)
            if base:
                cands.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    else:
        cands += [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
                  Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for exe in cands:
        if exe.exists():
            return [str(exe), f"--app={url}", "--window-size=560,300"]
    return None


class GoogleSpeechServer:
    """127.0.0.1 전용 웹서버. 페이지 상태(접속 여부)와 받아쓰기 이벤트를 중계한다."""

    def __init__(self):
        self.active = False
        self.session = 0
        self.last_poll = 0.0
        self.handler: Callable[[dict], None] | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def page_connected(self) -> bool:
        return time.time() - self.last_poll < 2.0

    def start(self) -> None:
        if self.httpd:
            return
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/":
                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif self.path == "/state":
                    server.last_poll = time.time()
                    body = json.dumps({"active": server.active, "session": server.session}).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(404, b"", "text/plain")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if self.path != "/event" or n > 100_000:
                    self._send(400, b"", "text/plain")
                    return
                try:
                    ev = json.loads(self.rfile.read(n))
                except ValueError:
                    self._send(400, b"", "text/plain")
                    return
                if server.handler and ev.get("session") == server.session:
                    server.handler(ev)
                self._send(204, b"", "text/plain")

        last_err = None
        for port in PORTS:
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
                self.port = port
                break
            except OSError as e:
                last_err = e
        if not self.httpd:
            raise RuntimeError(f"음성인식 연결용 포트를 열 수 없습니다: {last_err}")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def open_browser(self) -> str:
        cmd = _browser_cmd(self.url)
        if cmd:
            subprocess.Popen(cmd)
            return Path(cmd[0]).stem
        webbrowser.open(self.url)
        return "기본 브라우저"

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


class GoogleLive:
    """LiveTranscriber 와 같은 사용법(finish/join/text/lines/error)의 Google 음성인식 자막."""

    def __init__(self, server: GoogleSpeechServer, elapsed: Callable[[], float],
                 on_final: Callable[[int, str], None], on_partial: Callable[[str], None],
                 on_status: Callable[[str], None], log: Callable[[str], None] = lambda s: None):
        self.server, self.elapsed = server, elapsed
        self.on_final, self.on_partial, self.on_status, self.log = on_final, on_partial, on_status, log
        self.finals: dict[int, str] = {}
        self.error: Exception | None = None
        self._cid = 0
        self._utt_start: float | None = None  # 현재 문장이 시작된 녹음 시각
        self._done = threading.Event()
        self._last_event = 0.0
        self._partial = ""  # 아직 확정되지 않은 글자 (종료 시 보존)
        self._t_last = 0.0  # 마지막으로 확인한 녹음 시각 (녹음 종료 후 도착한 문장 시각용)

    @property
    def lines(self) -> list[str]:
        return [v for _, v in sorted(self.finals.items()) if v]

    def text(self) -> str:
        return "\n".join(self.lines)

    def start(self) -> None:
        from . import stt
        self._fmt = stt.fmt_ts
        srv = self.server
        srv.start()
        srv.session += 1
        srv.handler = self._on_event
        srv.active = True
        if not srv.page_connected:
            name = srv.open_browser()
            self.on_status(f"Google 음성인식: {name} 창을 여는 중... (처음엔 마이크 '허용' 클릭)")
            self.log(f"   Google 음성인식 창 열기: {srv.url}")
        else:
            self.on_status("Google 음성인식: 연결됨")

    def _now(self) -> float:
        e = self.elapsed()
        if e > 0:
            self._t_last = e
        return self._t_last

    def _on_event(self, ev: dict) -> None:
        self._last_event = time.time()
        kind, text = ev.get("type"), (ev.get("text") or "").strip()
        if kind == "partial":
            if text and self._utt_start is None:
                self._utt_start = max(0.0, self._now() - 1.0)  # 첫 글자는 말 시작 약 1초 뒤 도착
            self._partial = text
            self.on_partial(text)
        elif kind == "final" and text:
            self._add_final(text)
        elif kind == "status":
            self.on_status("Google 음성인식: 받아쓰는 중 (말하는 즉시 표시)")
        elif kind == "error" and text not in ("no-speech", "aborted"):
            self.log(f"   Google 음성인식 오류: {text}")
            msg = {"network": "인터넷 연결 확인 필요", "not-allowed": "Chrome 마이크 허용 필요",
                   "audio-capture": "Chrome 에서 마이크를 찾을 수 없음"}.get(text, text)
            self.on_status(f"Google 음성인식: {msg}")

    def _add_final(self, text: str) -> None:
        start = self._utt_start if self._utt_start is not None else max(0.0, self._now() - 2.0)
        self._utt_start = None
        self._partial = ""
        self._cid += 1
        line = f"[{self._fmt(start)}] {text}"
        self.finals[self._cid] = line
        self.on_partial("")
        self.on_final(self._cid, line)

    def finish(self) -> None:
        """녹음 종료: 받아쓰기 중지. 마지막 문장이 도착할 때까지 잠시 기다린 뒤 완료."""
        self.server.active = False

        def wait():
            t0 = time.time()
            while time.time() - t0 < 3.0:  # 마지막 확정 문장 대기 (최대 3초, 마지막 이벤트 후 1초 조용하면 종료)
                if time.time() - max(self._last_event, t0) > 1.0:
                    break
                time.sleep(0.1)
            self.server.handler = None
            if self._partial:  # 문장 도중에 녹음을 멈춘 경우 받아쓴 글자 보존
                self._add_final(self._partial)
            if not self.finals:
                self.error = RuntimeError("Google 음성인식 결과 없음 (창 연결·마이크 허용·인터넷 확인)")
            self.on_status("Google 음성인식: 완료")
            self._done.set()

        threading.Thread(target=wait, daemon=True).start()

    def join(self, timeout: float | None = None) -> None:
        self._done.wait(timeout)
