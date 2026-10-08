"""SAM4S AI 회의록 — 데스크톱 프로그램 (Tkinter).

탭 구성: 🎙 회의 녹음 | 📄 회의록 | ✅ Action Item | 🔍 검색 | ⚙ 설정
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, config, pipeline, rules
from .exporters import STATUS_ICON, export_action_tracker, safe_filename
from .storage import STATUSES, Store

FONT = "맑은 고딕" if sys.platform == "win32" else "TkDefaultFont"
STATUS_BG = {"완료": "#C6EFCE", "진행중": "#FFEB9C", "미착수": "#FFFFFF", "보류": "#E0E0E0", "지연": "#FFC7CE"}


def open_path(p: str | Path) -> None:
    p = str(p)
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def fmt_hms(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"SAM4S AI 회의록 v{__version__}")
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.settings = config.load()
        self.store = Store(self.settings["db_path"])
        self.recorder = None
        self.audio_path = ""
        self.audio_duration = 0.0
        self.transcript_text = ""
        self.current_meeting_id: int | None = None
        self.busy = False
        self.ui_q: queue.Queue = queue.Queue()
        self.log_hist: list[str] = []
        self.live = None  # 실시간 자막 (LiveTranscriber)

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=(FONT, 10))
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=(FONT, 10, "bold"))
        style.configure("Big.TButton", font=(FONT, 12, "bold"), padding=8)
        style.configure("Timer.TLabel", font=(FONT, 30, "bold"), foreground="#1F3864")
        style.configure("H.TLabel", font=(FONT, 11, "bold"), foreground="#1F3864")
        style.configure("KPI.TLabel", font=(FONT, 11, "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._build_record_tab()
        self._build_minutes_tab()
        self._build_actions_tab()
        self._build_search_tab()
        self._build_settings_tab()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_ui)
        self.after(500, self._tick)
        self.refresh_meetings()
        self.refresh_actions()

    # ================================================================ 탭1: 녹음
    def _build_record_tab(self):
        f = ttk.Frame(self.nb, padding=12)
        self.nb.add(f, text="  🎙 회의 녹음  ")

        meta = ttk.LabelFrame(f, text="회의 정보", padding=10)
        meta.pack(fill="x")
        self.v_title = tk.StringVar()
        self.v_date = tk.StringVar(value=date.today().isoformat())
        self.v_attendees = tk.StringVar()
        self.v_location = tk.StringVar()
        for r, (lbl, var, w) in enumerate([("회의명", self.v_title, 50), ("회의일", self.v_date, 14),
                                           ("참석자", self.v_attendees, 50), ("장소", self.v_location, 30)]):
            ttk.Label(meta, text=lbl).grid(row=r // 2, column=(r % 2) * 2, sticky="e", padx=(0, 6), pady=3)
            ttk.Entry(meta, textvariable=var, width=w).grid(row=r // 2, column=(r % 2) * 2 + 1, sticky="w", pady=3)
        ttk.Label(meta, text="※ 참석자 이름·직급을 적으면 음성인식과 담당자 추출이 정확해집니다 (예: 김철수 과장, 박민수 대리)",
                  foreground="#666").grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        rec = ttk.LabelFrame(f, text="녹음", padding=10)
        rec.pack(fill="x", pady=8)
        btns = ttk.Frame(rec)
        btns.pack(side="left")
        self.b_rec = ttk.Button(btns, text="● 녹음 시작", style="Big.TButton", command=self.start_recording)
        self.b_pause = ttk.Button(btns, text="❚❚ 일시정지", command=self.toggle_pause, state="disabled")
        self.b_stop = ttk.Button(btns, text="■ 녹음 종료", style="Big.TButton", command=self.stop_recording,
                                 state="disabled")
        self.b_rec.grid(row=0, column=0, padx=4)
        self.b_pause.grid(row=0, column=1, padx=4)
        self.b_stop.grid(row=0, column=2, padx=4)
        ttk.Button(btns, text="📂 음성파일 불러오기", command=self.load_audio).grid(row=1, column=0, padx=4, pady=6)
        ttk.Button(btns, text="📝 전사문(txt) 불러오기", command=self.load_transcript).grid(row=1, column=1, padx=4,
                                                                                       columnspan=2, sticky="w")
        tm = ttk.Frame(rec)
        tm.pack(side="left", padx=30)
        self.l_timer = ttk.Label(tm, text="00:00:00", style="Timer.TLabel")
        self.l_timer.pack()
        self.pb_level = ttk.Progressbar(tm, length=220, maximum=100)
        self.pb_level.pack(pady=2)
        ttk.Label(tm, text="마이크 입력 레벨", foreground="#666").pack()
        self.l_source = ttk.Label(rec, text="입력: (없음)", foreground="#444", wraplength=360)
        self.l_source.pack(side="left", padx=10)

        gen = ttk.Frame(f)
        gen.pack(fill="x", pady=4)
        self.b_txt = ttk.Button(gen, text="📝 텍스트만 변환", style="Big.TButton",
                                command=lambda: self.generate(minutes=False))
        self.b_txt.pack(side="left")
        self.b_gen = ttk.Button(gen, text="🤖  AI 회의록 생성", style="Big.TButton", command=self.generate)
        self.b_gen.pack(side="left", padx=(6, 0))
        self.v_autogen = tk.BooleanVar(value=True)
        ttk.Checkbutton(gen, text="녹음 종료 시 회의록까지 자동 생성", variable=self.v_autogen).pack(side="left", padx=12)
        self.pb_job = ttk.Progressbar(gen, length=320, maximum=100)
        self.pb_job.pack(side="left", padx=10)
        self.l_job = ttk.Label(gen, text="대기")
        self.l_job.pack(side="left")

        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True, pady=(6, 0))
        tf = ttk.LabelFrame(pw, text="📝 인식 텍스트 (TXT) — 음성인식 중 실시간 표시, 직접 수정 가능", padding=6)
        pw.add(tf, weight=3)
        tb = ttk.Frame(tf)
        tb.pack(fill="x")
        ttk.Button(tb, text="💾 TXT 저장", command=self.save_text).pack(side="left")
        ttk.Button(tb, text="📂 TXT 열기", command=self.open_text).pack(side="left", padx=4)
        ttk.Button(tb, text="📋 복사", command=self.copy_text).pack(side="left")
        self.l_txt = ttk.Label(tb, text="", foreground="#666")
        self.l_txt.pack(side="left", padx=10)
        self.t_text = tk.Text(tf, height=10, font=(FONT, 11), wrap="word", undo=True)
        sbt = ttk.Scrollbar(tf, command=self.t_text.yview)
        self.t_text.configure(yscrollcommand=sbt.set)
        self.t_text.pack(side="left", fill="both", expand=True, pady=(4, 0))
        sbt.pack(side="right", fill="y")
        self.text_ready = False  # 인식 텍스트가 현재 입력(녹음/파일)의 결과인지
        self.text_path = ""

        lg = ttk.LabelFrame(pw, text="진행 로그", padding=6)
        pw.add(lg, weight=1)
        self.t_log = tk.Text(lg, height=5, font=(FONT, 9), wrap="word", state="disabled")
        sb = ttk.Scrollbar(lg, command=self.t_log.yview)
        self.t_log.configure(yscrollcommand=sb.set)
        self.t_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------------------------------------------------------- 인식 텍스트
    def _set_text(self, text: str, ready: bool):
        self.t_text.delete("1.0", "end")
        if text:
            self.t_text.insert("1.0", text)
        self.text_ready = ready
        self.text_path = ""
        self.l_txt.configure(text="")

    def _text_file_path(self) -> Path:
        d = self.v_date.get().strip() or date.today().isoformat()
        name = f"{d}_{safe_filename(self.v_title.get().strip() or '회의')}_전사문.txt"
        return Path(self.settings["output_dir"]) / name

    def _write_text(self, text: str) -> Path:
        p = self._text_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        head = (f"회의명: {self.v_title.get().strip() or '-'}\n회의일: {self.v_date.get().strip()}\n"
                f"참석자: {self.v_attendees.get().strip() or '-'}\n\n")
        p.write_text(head + text.strip() + "\n", encoding="utf-8-sig")  # BOM: 메모장·엑셀 한글 깨짐 방지
        self.text_path = str(p)
        self.l_txt.configure(text=f"저장됨: {p.name}")
        return p

    def save_text(self):
        text = self.t_text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("안내", "저장할 텍스트가 없습니다.")
            return
        p = self._write_text(text)
        self.log(f"TXT 저장: {p}")

    def open_text(self):
        if not self.text_path and self.t_text.get("1.0", "end").strip():
            self._write_text(self.t_text.get("1.0", "end"))
        if self.text_path:
            open_path(self.text_path)

    def copy_text(self):
        self.clipboard_clear()
        self.clipboard_append(self.t_text.get("1.0", "end").strip())
        self.l_txt.configure(text="클립보드에 복사됨")

    def log(self, msg: str):
        """워커 스레드에서도 호출 가능."""
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.log_hist.append(line)  # 오류 기록용 사본 (워커 스레드에서 Tk 위젯을 읽지 않기 위함)
        self.ui_q.put(("log", line))

    def _append_log(self, msg: str):
        self.t_log.configure(state="normal")
        self.t_log.insert("end", msg + "\n")
        self.t_log.see("end")
        self.t_log.configure(state="disabled")

    def start_recording(self):
        try:
            from .recorder import Recorder
            self.recorder = Recorder(self.settings.get("mic_device"))
            title = safe_filename(self.v_title.get().strip() or "회의")
            path = Path(self.settings["recordings_dir"]) / f"{datetime.now():%Y%m%d_%H%M%S}_{title}.wav"
            live_q = queue.Queue() if self.settings.get("live_stt", True) else None
            self.recorder.live_q = live_q  # 녹음 시작 전에 연결해야 첫 마디부터 자막에 포함됨
            self.recorder.start(path)
        except Exception as e:
            self.recorder = None
            messagebox.showerror("녹음 오류", f"마이크를 열 수 없습니다.\n\n{e}\n\n설정 탭에서 마이크를 확인하세요.")
            return
        self.audio_path, self.transcript_text = "", ""
        self._set_text("", False)
        self.b_rec.configure(state="disabled")
        self.b_pause.configure(state="normal", text="❚❚ 일시정지")
        self.b_stop.configure(state="normal")
        self.b_gen.configure(state="disabled")
        self.b_txt.configure(state="disabled")
        self.l_source.configure(text=f"녹음 중 → {path.name}")
        self.log(f"녹음 시작 ({self.recorder.samplerate} Hz)")
        if live_q is not None:
            from . import stt
            from .live import LiveTranscriber
            self.live = LiveTranscriber(
                self.recorder.samplerate, self.settings["whisper_model"],
                stt.build_prompt(self.settings.get("vocab", ""), self.v_attendees.get().strip()),
                on_line=lambda ln: self.ui_q.put(("line", ln)),
                on_status=lambda st: self.ui_q.put(("live_status", st)), log=self.log, q=live_q)
            self.live.start()

    def toggle_pause(self):
        if not self.recorder:
            return
        if self.recorder.paused:
            self.recorder.resume()
            self.b_pause.configure(text="❚❚ 일시정지")
            self.log("녹음 재개")
        else:
            self.recorder.pause()
            self.b_pause.configure(text="▶ 재개")
            self.log("녹음 일시정지")

    def stop_recording(self):
        if not self.recorder:
            return
        path, dur = self.recorder.stop()
        self.recorder = None
        self.audio_path, self.audio_duration = str(path), dur
        self.b_rec.configure(state="normal")
        self.b_pause.configure(state="disabled", text="❚❚ 일시정지")
        self.b_stop.configure(state="disabled")
        self.b_gen.configure(state="normal")
        self.b_txt.configure(state="normal")
        self.pb_level["value"] = 0
        self.l_source.configure(text=f"음성: {path.name}  ({fmt_hms(dur)})")
        self.log(f"녹음 종료 — {fmt_hms(dur)}, 저장: {path}")
        live, self.live = self.live, None
        minutes = self.v_autogen.get()
        if live is None:
            # 녹음 종료 → 항상 텍스트(TXT)부터 만들고, 옵션이 켜져 있으면 회의록까지 이어서 작성
            self.generate(minutes=minutes)
            return
        live.finish()
        args = self._job_args()
        if args is None:
            return
        self._set_busy("남은 음성 자막 처리 중...")
        threading.Thread(target=self._finish_live, args=(live, args, minutes), daemon=True).start()

    def _finish_live(self, live, args: dict, minutes: bool):
        """실시간 자막을 마무리하고 그 텍스트로 TXT/회의록 작성. 자막 실패·옵션 시 전체 다시 인식."""
        live.join()
        text = live.text()
        if live.error or not text.strip() or self.settings.get("final_full_pass"):
            if text.strip() and not live.error:
                self.log("정확도 향상을 위해 녹음 전체를 다시 인식합니다 (설정: 종료 후 전체 다시 인식)")
            args["transcript"] = ""
        else:
            self.log(f"[1/3] 실시간 자막 완료: {len(live.lines)}문장")
            self.ui_q.put(("text_done", text))
            args["transcript"] = text
        self._worker(args, minutes)

    def load_audio(self):
        p = filedialog.askopenfilename(title="회의 음성파일 선택", filetypes=[
            ("음성파일", "*.wav *.mp3 *.m4a *.aac *.ogg *.flac *.wma *.mp4 *.webm"), ("모든 파일", "*.*")])
        if p:
            self.audio_path, self.transcript_text, self.audio_duration = p, "", 0.0
            self._set_text("", False)
            self.l_source.configure(text=f"음성: {Path(p).name}")
            if not self.v_title.get():
                self.v_title.set(Path(p).stem)
            self.log(f"음성파일 선택: {p}")

    def load_transcript(self):
        p = filedialog.askopenfilename(title="전사문 텍스트 선택", filetypes=[("텍스트", "*.txt"), ("모든 파일", "*.*")])
        if not p:
            return
        raw = Path(p).read_bytes()
        for enc in ("utf-8-sig", "cp949"):
            try:
                self.transcript_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        self.audio_path = ""
        self._set_text(self.transcript_text, True)
        self.l_source.configure(text=f"전사문: {Path(p).name} ({len(self.transcript_text):,}자)")
        if not self.v_title.get():
            self.v_title.set(Path(p).stem)
        self.log(f"전사문 불러옴: {p}")

    def generate(self, minutes: bool = True):
        """minutes=False → 음성인식(TXT)까지만. 이미 인식된 텍스트가 있으면(수정본 포함) 음성인식 생략."""
        if self.busy:
            return
        pane = self.t_text.get("1.0", "end").strip()
        if self.text_ready and pane:
            if not minutes:
                self.save_text()
                return
            self.transcript_text = pane
        if not self.audio_path and not self.transcript_text:
            messagebox.showinfo("안내", "먼저 녹음하거나 음성파일/전사문을 불러오세요.")
            return
        args = self._job_args()
        if args is None:
            return
        self._set_busy("처리 중...")
        threading.Thread(target=self._worker, args=(args, minutes), daemon=True).start()

    def _job_args(self) -> dict | None:
        try:
            mdate = date.fromisoformat(self.v_date.get().strip())
        except ValueError:
            messagebox.showerror("입력 오류", "회의일은 YYYY-MM-DD 형식으로 입력하세요.")
            return None
        title = self.v_title.get().strip() or f"회의 {mdate.isoformat()}"
        return dict(title=title, meeting_date=mdate, attendees=self.v_attendees.get().strip(),
                    location=self.v_location.get().strip(), audio_path=self.audio_path,
                    transcript=self.transcript_text, duration_sec=self.audio_duration)

    def _set_busy(self, msg: str):
        self.busy = True
        self.b_gen.configure(state="disabled")
        self.b_txt.configure(state="disabled")
        self.pb_job["value"] = 0
        self.l_job.configure(text=msg)

    def _worker(self, args: dict, minutes: bool = True):
        def progress(p: float, line: str):
            self.ui_q.put(("progress", p))
            if line:
                self.ui_q.put(("line", line))

        try:
            if not args["transcript"]:
                from . import stt
                self.ui_q.put(("text_clear", None))
                self.log(f"[1/3] 음성인식 시작 (모델: {self.settings['whisper_model']}) — 최초 1회는 모델 다운로드로 시간이 걸립니다.")
                text, dur = stt.transcribe(args["audio_path"], self.settings["whisper_model"],
                                           self.settings.get("vocab", ""), args["attendees"], progress, log=self.log)
                if not text.strip():
                    raise RuntimeError("인식된 음성이 없습니다. 마이크 입력/녹음파일을 확인하세요.")
                args["transcript"], args["duration_sec"] = text, dur or args["duration_sec"]
                self.log(f"[1/3] 음성인식 완료: {len(text.splitlines())}문장")
                self.ui_q.put(("text_done", text))
            if not minutes:
                self.ui_q.put(("idle", "텍스트 변환 완료"))
                return
            mid = pipeline.process(self.store, self.settings, log=self.log, progress=progress, **args)
            paths = pipeline.export(self.store, mid, self.settings["output_dir"], ("xlsx",))
            self.log(f"Excel 회의록 저장: {paths[0]}")
            self.ui_q.put(("done", mid))
        except Exception as e:
            self.log("오류: " + "".join(traceback.format_exception_only(type(e), e)).strip())
            self._write_error_log()
            self.ui_q.put(("fail", f"{e}\n\n자세한 내용: {config.DATA_DIR / 'error_log.txt'}"))

    def _write_error_log(self):
        """원인 파악용 상세 기록 (이 파일을 보내주면 원격으로 진단 가능)."""
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.DATA_DIR / "error_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S}  v{__version__}  "
                        f"모델={self.settings.get('whisper_model')} 마이크={self.settings.get('mic_device')}\n")
                f.write(traceback.format_exc())
                f.write("--- 진행 로그 ---\n" + "\n".join(self.log_hist[-60:]) + "\n")
        except Exception:
            pass

    # ================================================================ 탭2: 회의록
    def _build_minutes_tab(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="  📄 회의록  ")
        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)

        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        ttk.Label(left, text="회의 목록", style="H.TLabel").pack(anchor="w")
        self.tv_meet = ttk.Treeview(left, columns=("date", "title", "open"), show="headings", height=20)
        for c, t, w in (("date", "회의일", 90), ("title", "회의명", 200), ("open", "미결/전체", 70)):
            self.tv_meet.heading(c, text=t)
            self.tv_meet.column(c, width=w, anchor="center" if c != "title" else "w")
        self.tv_meet.pack(fill="both", expand=True)
        self.tv_meet.bind("<<TreeviewSelect>>", lambda e: self._on_meeting_select())
        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=4)
        ttk.Button(lb, text="새로고침", command=self.refresh_meetings).pack(side="left")
        ttk.Button(lb, text="삭제", command=self.delete_meeting).pack(side="right")

        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        bar = ttk.Frame(right)
        bar.pack(fill="x")
        for txt, fmt in (("📊 Excel 저장", "xlsx"), ("📝 Word 저장", "docx"), ("📕 PDF 저장", "pdf"),
                         ("🗒 TXT 저장", "txt")):
            ttk.Button(bar, text=txt, command=lambda x=fmt: self.export_current(x)).pack(side="left", padx=2)
        ttk.Button(bar, text="📁 출력 폴더", command=lambda: open_path(self._ensure_dir(self.settings["output_dir"]))
                   ).pack(side="left", padx=8)
        ttk.Button(bar, text="🗒 전사문 보기", command=self.show_transcript).pack(side="left")
        ttk.Button(bar, text="🔁 AI 재작성", command=self.regenerate_current).pack(side="right")

        self.t_min = tk.Text(right, font=(FONT, 10), wrap="word", height=18, padx=10, pady=8)
        self.t_min.tag_configure("h1", font=(FONT, 15, "bold"), foreground="#1F3864", spacing3=6)
        self.t_min.tag_configure("h2", font=(FONT, 11, "bold"), foreground="#1F3864", spacing1=8, spacing3=2)
        self.t_min.tag_configure("meta", foreground="#555")
        self.t_min.pack(fill="both", expand=True, pady=4)

        ttk.Label(right, text="Action Item (더블클릭: 편집)", style="H.TLabel").pack(anchor="w")
        self.tv_mact = self._action_tree(right, height=6, with_meeting=False)

    def _ensure_dir(self, p: str) -> str:
        Path(p).mkdir(parents=True, exist_ok=True)
        return p

    def refresh_meetings(self):
        self.tv_meet.delete(*self.tv_meet.get_children())
        for m in self.store.list_meetings():
            self.tv_meet.insert("", "end", iid=str(m["id"]),
                                values=(m["meeting_date"], m["title"], f"{m['n_open']}/{m['n_actions']}"))

    def _on_meeting_select(self):
        sel = self.tv_meet.selection()
        if sel:
            self.show_meeting(int(sel[0]))

    def show_meeting(self, mid: int):
        m = self.store.get_meeting(mid)
        if not m:
            return
        self.current_meeting_id = mid
        mi = m["minutes"]
        t = self.t_min
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", f"{m['title']}\n", "h1")
        t.insert("end", f"일시 {m['meeting_date']}   참석 {m['attendees'] or '-'}   장소 {m['location'] or '-'}"
                        f"   작성 {mi.get('_engine', '')}\n", "meta")

        def sec(title, items):
            t.insert("end", f"■ {title}\n", "h2")
            for it in items or ["-"]:
                t.insert("end", f"  • {it}\n")

        sec("Executive Summary", mi.get("summary"))
        sec("회의 목적", [mi.get("purpose")] if mi.get("purpose") else [])
        sec("주요 논의사항", [f"[{d['topic']}] {d['content']}" for d in mi.get("discussions", [])])
        sec("결정사항", mi.get("decisions"))
        sec("문제점", mi.get("issues"))
        sec("미결사항", mi.get("pending"))
        if mi.get("next_meeting"):
            sec("차기 회의", [mi["next_meeting"]])
        t.configure(state="disabled")
        self._fill_action_tree(self.tv_mact, m["actions"], with_meeting=False)
        # 이미 선택된 상태에서 다시 selection_set 하면 <<TreeviewSelect>> → show_meeting 무한 반복
        if self.tv_meet.exists(str(mid)) and self.tv_meet.selection() != (str(mid),):
            self.tv_meet.selection_set(str(mid))
            self.tv_meet.see(str(mid))

    def export_current(self, fmt: str):
        if not self.current_meeting_id:
            messagebox.showinfo("안내", "회의를 먼저 선택하세요.")
            return
        try:
            p = pipeline.export(self.store, self.current_meeting_id, self.settings["output_dir"], (fmt,))[0]
        except PermissionError:
            messagebox.showerror("저장 오류", "같은 이름의 파일이 열려 있습니다. 파일을 닫고 다시 시도하세요.")
            return
        if messagebox.askyesno("저장 완료", f"{p}\n\n파일을 여시겠습니까?"):
            open_path(p)

    def show_transcript(self):
        if not self.current_meeting_id:
            return
        m = self.store.get_meeting(self.current_meeting_id)
        w = tk.Toplevel(self)
        w.title(f"전사문 — {m['title']}")
        w.geometry("820x600")
        tx = tk.Text(w, font=(FONT, 10), wrap="word", padx=8, pady=8)
        tx.insert("1.0", m["transcript"])
        tx.pack(fill="both", expand=True)

    def regenerate_current(self):
        """전사문은 그대로 두고 회의록만 다시 작성 (설정 변경/API 키 입력 후 등)."""
        if not self.current_meeting_id or self.busy:
            return
        if not messagebox.askyesno("AI 재작성", "회의록과 Action Item 을 다시 작성합니다.\n"
                                              "기존 Action Item 의 상태/메모는 초기화됩니다. 계속할까요?"):
            return
        mid = self.current_meeting_id
        m = self.store.get_meeting(mid)
        self.busy = True

        def work():
            try:
                from . import summarizer
                mins = summarizer.summarize(m["transcript"], date.fromisoformat(m["meeting_date"]), m["title"],
                                            m["attendees"], self.settings.get("use_ai", True),
                                            self.settings.get("ai_effort", "medium"), self.log)
                self.store.update_minutes(mid, mins)
                self.ui_q.put(("done", mid))
            except Exception as e:
                self.ui_q.put(("fail", str(e)))

        self.log(f"회의 #{mid} 회의록 재작성 시작")
        threading.Thread(target=work, daemon=True).start()

    def delete_meeting(self):
        sel = self.tv_meet.selection()
        if sel and messagebox.askyesno("삭제", "선택한 회의와 Action Item 을 삭제할까요?\n(녹음파일은 남겨둡니다)"):
            self.store.delete_meeting(int(sel[0]))
            self.current_meeting_id = None
            self.t_min.configure(state="normal")
            self.t_min.delete("1.0", "end")
            self.refresh_meetings()
            self.refresh_actions()

    # ================================================================ 탭3: Action Item
    def _build_actions_tab(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="  ✅ Action Item  ")
        self.l_kpi = ttk.Label(f, text="", style="KPI.TLabel")
        self.l_kpi.pack(anchor="w", pady=(0, 6))
        flt = ttk.Frame(f)
        flt.pack(fill="x")
        ttk.Label(flt, text="담당자").pack(side="left")
        self.v_flt_person = tk.StringVar()
        self.cb_person = ttk.Combobox(flt, textvariable=self.v_flt_person, width=14)
        self.cb_person.pack(side="left", padx=4)
        self.cb_person.bind("<<ComboboxSelected>>", lambda e: self.refresh_actions())
        self.cb_person.bind("<Return>", lambda e: self.refresh_actions())
        self.v_only_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(flt, text="미결만 보기", variable=self.v_only_open, command=self.refresh_actions
                        ).pack(side="left", padx=10)
        ttk.Button(flt, text="새로고침", command=self.refresh_actions).pack(side="left")
        ttk.Button(flt, text="📊 현황 Excel 저장", command=self.export_tracker).pack(side="right")
        for st in reversed(STATUSES):
            ttk.Button(flt, text=f"{STATUS_ICON.get(st, '')} {st}",
                       command=lambda s=st: self.set_status_selected(s)).pack(side="right", padx=2)
        ttk.Label(flt, text="선택 항목 상태 →").pack(side="right", padx=4)
        self.tv_act = self._action_tree(f, height=22, with_meeting=True)

    def _action_tree(self, parent, height: int, with_meeting: bool) -> ttk.Treeview:
        cols = [("st", "상태", 70), ("who", "담당자", 90), ("task", "업무", 380), ("due", "기한", 95),
                ("pri", "우선", 50)]
        if with_meeting:
            cols += [("mt", "회의명", 180), ("md", "회의일", 90), ("note", "메모", 160)]
        fr = ttk.Frame(parent)
        fr.pack(fill="both", expand=True)
        tv = ttk.Treeview(fr, columns=[c[0] for c in cols], show="headings", height=height)
        for c, t, w in cols:
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor="w" if c in ("task", "mt", "note") else "center")
        for st, bg in STATUS_BG.items():
            tv.tag_configure(st, background=bg)
        sb = ttk.Scrollbar(fr, command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv.bind("<Double-1>", lambda e, t=tv: self.edit_action(t))
        return tv

    def _fill_action_tree(self, tv: ttk.Treeview, actions: list[dict], with_meeting: bool):
        tv.delete(*tv.get_children())
        for a in actions:
            st = a["display_status"]
            vals = [f"{STATUS_ICON.get(st, '')} {st}", a["assignee"], a["task"], a["due_date"] or a["due_text"] or "-",
                    a["priority"]]
            if with_meeting:
                vals += [a["meeting_title"], a["meeting_date"], a.get("note", "")]
            tv.insert("", "end", iid=str(a["id"]), values=vals, tags=(st,))

    def refresh_actions(self):
        acts = self.store.list_actions(assignee=self.v_flt_person.get().strip(), only_open=self.v_only_open.get())
        self._fill_action_tree(self.tv_act, acts, with_meeting=True)
        k = self.store.action_kpi()
        self.l_kpi.configure(text=f"전체 {k['전체']}건   🟢 완료 {k['완료']}   🟡 진행중 {k['진행중']}   ⚪ 미착수 {k['미착수']}"
                                  f"   🔴 지연 {k['지연']}   |   완료율 {k['완료율']}%",
                             foreground="#C00000" if k["지연"] else "#1F3864")
        self.cb_person["values"] = [""] + sorted({a["assignee"] for a in self.store.list_actions()})

    def set_status_selected(self, status: str):
        sel = self.tv_act.selection()
        if not sel:
            messagebox.showinfo("안내", "상태를 바꿀 항목을 선택하세요 (Ctrl/Shift 로 여러 개 선택).")
            return
        for iid in sel:
            self.store.set_action_status(int(iid), status)
        self.refresh_actions()
        self.refresh_meetings()

    def edit_action(self, tv: ttk.Treeview):
        sel = tv.selection()
        if not sel:
            return
        aid = int(sel[0])
        a = next((x for x in self.store.list_actions() if x["id"] == aid), None)
        if not a:
            return
        w = tk.Toplevel(self)
        w.title("Action Item 편집")
        w.transient(self)
        w.grab_set()
        fields = [("담당자", "assignee"), ("업무", "task"), ("기한(YYYY-MM-DD)", "due_date"), ("메모", "note")]
        vars_ = {}
        for r, (lbl, key) in enumerate(fields):
            ttk.Label(w, text=lbl).grid(row=r, column=0, sticky="e", padx=8, pady=4)
            v = tk.StringVar(value=a.get(key) or "")
            ttk.Entry(w, textvariable=v, width=60).grid(row=r, column=1, padx=8, pady=4)
            vars_[key] = v
        v_pri = tk.StringVar(value=a["priority"])
        v_st = tk.StringVar(value=a["status"])
        ttk.Label(w, text="우선순위").grid(row=4, column=0, sticky="e", padx=8)
        ttk.Combobox(w, textvariable=v_pri, values=["높음", "보통", "낮음"], width=8, state="readonly"
                     ).grid(row=4, column=1, sticky="w", padx=8)
        ttk.Label(w, text="상태").grid(row=5, column=0, sticky="e", padx=8)
        ttk.Combobox(w, textvariable=v_st, values=list(STATUSES), width=8, state="readonly"
                     ).grid(row=5, column=1, sticky="w", padx=8, pady=4)

        def save():
            due = vars_["due_date"].get().strip()
            if due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    d = rules.parse_due(due, date.today())  # '다음 주 금요일' 같은 입력도 허용
                    if not d:
                        messagebox.showerror("입력 오류", "기한을 YYYY-MM-DD 로 입력하세요.", parent=w)
                        return
                    due = d.isoformat()
            self.store.update_action(aid, assignee=vars_["assignee"].get().strip() or "미정",
                                     task=vars_["task"].get().strip(), due_date=due, note=vars_["note"].get().strip(),
                                     priority=v_pri.get(), status=v_st.get())
            w.destroy()
            self.refresh_actions()
            self.refresh_meetings()
            if self.current_meeting_id:
                self.show_meeting(self.current_meeting_id)

        ttk.Button(w, text="저장", command=save).grid(row=6, column=1, sticky="e", padx=8, pady=8)

    def export_tracker(self):
        acts = self.store.list_actions(assignee=self.v_flt_person.get().strip(), only_open=self.v_only_open.get())
        p = export_action_tracker(acts, self.settings["output_dir"], self.store.action_kpi())
        if messagebox.askyesno("저장 완료", f"{p}\n\n파일을 여시겠습니까?"):
            open_path(p)

    # ================================================================ 탭4: 검색
    def _build_search_tab(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="  🔍 검색  ")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.v_q, font=(FONT, 12), width=60)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self.do_search())
        ttk.Button(top, text="검색", command=self.do_search).pack(side="left", padx=6)
        ttk.Label(f, text="예) 어제 회의에서 김과장에게 시킨 일 / 지난주 SMT 견적 / 박대리 원가 / 이번 달 불량",
                  foreground="#666").pack(anchor="w", pady=4)
        self.l_qinfo = ttk.Label(f, text="", foreground="#1F3864")
        self.l_qinfo.pack(anchor="w")
        ttk.Label(f, text="관련 Action Item", style="H.TLabel").pack(anchor="w", pady=(8, 0))
        self.tv_sact = self._action_tree(f, height=9, with_meeting=True)
        ttk.Label(f, text="관련 회의 (더블클릭: 회의록 열기)", style="H.TLabel").pack(anchor="w", pady=(8, 0))
        self.tv_smeet = ttk.Treeview(f, columns=("date", "title", "snip"), show="headings", height=7)
        for c, t, w in (("date", "회의일", 90), ("title", "회의명", 200), ("snip", "관련 발언", 700)):
            self.tv_smeet.heading(c, text=t)
            self.tv_smeet.column(c, width=w, anchor="w")
        self.tv_smeet.pack(fill="both", expand=True)
        self.tv_smeet.bind("<Double-1>", lambda ev: self._open_search_meeting())

    def do_search(self):
        q = self.v_q.get().strip()
        if not q:
            return
        p = rules.parse_search_query(q, date.today())
        cond = []
        if p["date_from"]:
            cond.append(f"기간 {p['date_from']} ~ {p['date_to']}")
        if p["people"]:
            cond.append("담당자 " + ", ".join(p["people"]))
        if p["keywords"]:
            cond.append("키워드 " + ", ".join(p["keywords"]))
        self.l_qinfo.configure(text="검색 조건: " + (" / ".join(cond) or "전체"))
        r = self.store.search(p["people"], p["keywords"], p["date_from"], p["date_to"])
        self._fill_action_tree(self.tv_sact, r["actions"], with_meeting=True)
        self.tv_smeet.delete(*self.tv_smeet.get_children())
        for m in r["meetings"]:
            self.tv_smeet.insert("", "end", iid=str(m["id"]), values=(m["meeting_date"], m["title"], m["snippet"]))

    def _open_search_meeting(self):
        sel = self.tv_smeet.selection()
        if sel:
            self.nb.select(1)
            self.show_meeting(int(sel[0]))

    # ================================================================ 탭5: 설정
    def _build_settings_tab(self):
        f = ttk.Frame(self.nb, padding=14)
        self.nb.add(f, text="  ⚙ 설정  ")
        s = self.settings
        self.v_model = tk.StringVar(value=s["whisper_model"])
        self.v_useai = tk.BooleanVar(value=s["use_ai"])
        self.v_effort = tk.StringVar(value=s["ai_effort"])
        self.v_key = tk.StringVar(value=s.get("api_key", ""))
        self.v_out = tk.StringVar(value=s["output_dir"])
        self.v_mic = tk.StringVar()
        self.v_live = tk.BooleanVar(value=s.get("live_stt", True))
        self.v_fullpass = tk.BooleanVar(value=s.get("final_full_pass", False))

        r = 0

        def row(label, widget, hint=""):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="ne", padx=8, pady=6)
            widget.grid(row=r, column=1, sticky="w", pady=6)
            if hint:
                ttk.Label(f, text=hint, foreground="#666").grid(row=r, column=2, sticky="w", padx=8)
            r += 1

        row("음성인식 모델", ttk.Combobox(f, textvariable=self.v_model, state="readonly", width=16,
                                       values=["small", "medium", "large-v3-turbo", "large-v3"]),
            "small: 빠름 / medium: 권장 / large-v3: 가장 정확(느림, GPU 권장)")
        self.mic_map = {"윈도우 기본 마이크": None}
        try:
            from .recorder import Recorder
            for i, name in Recorder.list_input_devices():
                self.mic_map[f"[{i}] {name}"] = i
        except Exception as e:
            self.mic_map[f"(마이크 목록 오류: {e})"] = None
        cur = next((k for k, v in self.mic_map.items() if v == s.get("mic_device")), "윈도우 기본 마이크")
        self.v_mic.set(cur)
        row("마이크", ttk.Combobox(f, textvariable=self.v_mic, values=list(self.mic_map), state="readonly", width=50),
            "회의용 컨퍼런스 마이크(USB) 사용 시 인식률 크게 향상")
        row("실시간 자막", ttk.Checkbutton(f, variable=self.v_live, text="녹음 중 말한 내용을 바로 텍스트로 표시"),
            "느린 PC 에서 자막이 밀리면 해제 (녹음 종료 후 한 번에 인식)")
        row("종료 후 재인식", ttk.Checkbutton(f, variable=self.v_fullpass, text="녹음 종료 후 전체를 다시 인식 (정확도↑)"),
            "실시간 자막보다 문맥이 이어져 정확, 녹음 길이의 0.3~0.5배 시간 추가")
        row("AI 회의록 사용", ttk.Checkbutton(f, variable=self.v_useai, text="Claude AI 로 회의록 작성 (해제 시 규칙 기반·완전 오프라인)"))
        row("AI 분석 수준", ttk.Combobox(f, textvariable=self.v_effort, values=["low", "medium", "high"],
                                       state="readonly", width=10), "high: 긴 회의·복잡한 안건에서 더 정확, 비용·시간 증가")
        row("Claude API 키", ttk.Entry(f, textvariable=self.v_key, show="•", width=52),
            "비우면 환경변수 ANTHROPIC_API_KEY 사용 (이 PC 설정파일에 저장됨)")
        outf = ttk.Frame(f)
        ttk.Entry(outf, textvariable=self.v_out, width=52).pack(side="left")
        ttk.Button(outf, text="찾기", command=lambda: self.v_out.set(filedialog.askdirectory() or self.v_out.get())
                   ).pack(side="left", padx=4)
        row("출력 폴더", outf)
        self.t_vocab = tk.Text(f, width=70, height=5, font=(FONT, 10))
        self.t_vocab.insert("1.0", s.get("vocab", ""))
        row("인식 보조 용어", self.t_vocab, "제품명·고객사·사람 이름 등 (쉼표 구분)\n비우면 기본 제조용어 사용")
        ttk.Button(f, text="💾 설정 저장", style="Big.TButton", command=self.save_settings).grid(row=r, column=1,
                                                                                            sticky="w", pady=12)
        ttk.Label(f, text=f"데이터: {Path(s['db_path']).parent}\n녹음파일: {s['recordings_dir']}",
                  foreground="#666").grid(row=r + 1, column=1, sticky="w")

    def save_settings(self):
        s = self.settings
        s.update(whisper_model=self.v_model.get(), use_ai=self.v_useai.get(), ai_effort=self.v_effort.get(),
                 api_key=self.v_key.get().strip(), output_dir=self.v_out.get().strip() or s["output_dir"],
                 vocab=self.t_vocab.get("1.0", "end").strip(), mic_device=self.mic_map.get(self.v_mic.get()),
                 live_stt=self.v_live.get(), final_full_pass=self.v_fullpass.get())
        config.save(s)
        messagebox.showinfo("설정", "저장했습니다.")

    # ================================================================ 루프
    def _tick(self):
        if self.recorder and self.recorder.recording:
            self.l_timer.configure(text=fmt_hms(self.recorder.elapsed()))
            self.pb_level["value"] = 0 if self.recorder.paused else self.recorder.level * 100
        self.after(200, self._tick)

    def _poll_ui(self):
        try:
            while True:
                kind, val = self.ui_q.get_nowait()
                if kind == "log":
                    self._append_log(val)
                elif kind == "line":
                    self.t_text.insert("end", val + "\n")
                    self.t_text.see("end")
                elif kind == "live_status":
                    self.l_txt.configure(text=val)
                elif kind == "text_clear":
                    self._set_text("", False)
                elif kind == "text_done":
                    self._set_text(val, True)
                    p = self._write_text(val)
                    self.log(f"TXT 저장: {p}")
                elif kind == "idle":
                    self.busy = False
                    self.b_gen.configure(state="normal")
                    self.b_txt.configure(state="normal")
                    self.pb_job["value"] = 100
                    self.l_job.configure(text=val)
                elif kind == "progress":
                    self.pb_job["value"] = val * 100
                    self.l_job.configure(text=f"음성인식 {val * 100:.0f}%")
                elif kind == "done":
                    self.busy = False
                    self.b_gen.configure(state="normal")
                    self.b_txt.configure(state="normal")
                    self.pb_job["value"] = 100
                    self.l_job.configure(text="완료")
                    self.refresh_meetings()
                    self.refresh_actions()
                    self.nb.select(1)
                    self.show_meeting(val)
                elif kind == "fail":
                    self.busy = False
                    self.b_gen.configure(state="normal")
                    self.b_txt.configure(state="normal")
                    self.l_job.configure(text="실패")
                    messagebox.showerror("회의록 생성 실패", val)
        except queue.Empty:
            pass
        self.after(100, self._poll_ui)

    def _on_close(self):
        if self.recorder and self.recorder.recording:
            if not messagebox.askyesno("종료", "녹음 중입니다. 녹음을 저장하고 종료할까요?"):
                return
            path, _ = self.recorder.stop()
            if self.live:
                self.live.finish()
            messagebox.showinfo("녹음 저장", f"녹음파일이 저장되었습니다.\n{path}\n\n다음 실행 시 '음성파일 불러오기'로 처리하세요.")
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
