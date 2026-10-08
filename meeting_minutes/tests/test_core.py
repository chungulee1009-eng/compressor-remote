"""핵심 로직 테스트: python -m pytest meeting_minutes/tests -q"""
from datetime import date

import time

import numpy as np
import pytest

from meeting_minutes import exporters, pipeline, rules, summarizer
from meeting_minutes.storage import Store, display_status

BASE = date(2026, 10, 7)  # 수요일

SAMPLE = """[00:00:05] 오늘은 SMT 이전 건하고 10월 생산계획 논의하겠습니다.
[00:00:12] SMT 이전은 11월까지 검토하기로 했습니다. 김과장이 업체 3곳 견적을 받아주세요. 다음 주 화요일까지 부탁합니다.
[00:01:02] 박대리는 KIOSK 원가자료를 이번 주 금요일까지 작성해 주세요.
[00:01:30] 라인2 불량률이 2.3%로 목표 1.5% 초과 문제가 있습니다. 이차장님이 생산 CAPA 검토 10/15까지 보고해 주세요.
[00:02:10] 10월 POS 생산은 월 1만2천대로 확정합니다."""


@pytest.mark.parametrize("text,expected", [
    ("다음 주 화요일까지", "2026-10-13"),
    ("담주 월요일", "2026-10-12"),
    ("이번 주 금요일까지", "2026-10-09"),
    ("금요일까지", "2026-10-09"),
    ("이번 주까지", "2026-10-09"),
    ("내일", "2026-10-08"),
    ("3일 후", "2026-10-10"),
    ("10/13", "2026-10-13"),
    ("10월 15일", "2026-10-15"),
    ("2026-11-02", "2026-11-02"),
    ("이번 달 말", "2026-10-31"),
    ("다음 달 말", "2026-11-30"),
    ("11월까지", "2026-11-30"),
    ("11월 말", "2026-11-30"),
    ("연말까지", "2026-12-31"),
])
def test_parse_due(text, expected):
    assert rules.parse_due(text, BASE).isoformat() == expected


def test_parse_due_none():
    assert rules.parse_due("검토 바랍니다", BASE) is None
    assert rules.parse_due("", BASE) is None


def test_parse_due_year_rollover():
    assert rules.parse_due("1/10", date(2026, 12, 20)) == date(2027, 1, 10)


def test_find_people():
    assert rules.find_people("김과장이 견적, 박민수 대리님은 원가, 이번 회의에서 이차장님") == ["김과장", "박민수대리", "이차장"]
    assert rules.find_people("이번 주 생산 계획") == []


def test_offline_minutes_actions():
    m = rules.offline_minutes(SAMPLE, BASE, "SMT 이전")
    acts = {a["assignee"]: a for a in m["action_items"]}
    assert set(acts) == {"김과장", "박대리", "이차장"}  # '논의하겠습니다' 는 Action 아님, 기한만 있는 문장은 병합
    assert acts["김과장"]["due_date"] == "2026-10-13"
    assert acts["박대리"]["due_date"] == "2026-10-09"
    assert acts["이차장"]["due_date"] == "2026-10-15"
    assert any("확정" in d for d in m["decisions"])
    assert any("불량률" in i for i in m["issues"])


def test_search_query():
    q = rules.parse_search_query("어제 회의에서 김과장에게 시킨 일이 뭐였지?", BASE)
    assert q["people"] == ["김과장"]
    assert q["date_from"] == q["date_to"] == date(2026, 10, 6)
    assert q["keywords"] == []


def test_normalize_fills_due_from_text():
    m = summarizer.normalize({"action_items": [{"assignee": "", "task": "견적", "due_text": "다음 주 화요일",
                                                "due_date": "모름"}]}, BASE)
    a = m["action_items"][0]
    assert a["assignee"] == "미정" and a["due_date"] == "2026-10-13"


def test_summarize_without_key_uses_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    logs = []
    m = summarizer.summarize(SAMPLE, BASE, "t", log=logs.append)
    assert m["_engine"] == "offline" and logs


def test_summarize_ai_failure_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    def boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(summarizer, "summarize_with_claude", boom)
    logs = []
    m = summarizer.summarize(SAMPLE, BASE, "t", log=logs.append)
    assert m["_engine"] == "offline" and "network down" in logs[0]


def test_display_status():
    assert display_status("미착수", "2026-10-01", BASE) == "지연"
    assert display_status("완료", "2026-10-01", BASE) == "완료"
    assert display_status("진행중", "2026-10-30", BASE) == "진행중"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def _settings(tmp_path):
    return {"use_ai": False, "ai_effort": "low", "whisper_model": "small", "output_dir": str(tmp_path / "out")}


def test_pipeline_store_search_export(store, tmp_path):
    mid = pipeline.process(store, _settings(tmp_path), title="SMT 이전 검토", meeting_date=BASE,
                           attendees="김과장, 박대리", transcript=SAMPLE, log=lambda s: None)
    m = store.get_meeting(mid)
    assert len(m["actions"]) == 3

    # 상태 변경 + KPI
    aid = m["actions"][0]["id"]
    store.set_action_status(aid, "완료")
    k = store.action_kpi(BASE)
    assert k["전체"] == 3 and k["완료"] == 1 and k["완료율"] == pytest.approx(33.3)
    assert len(store.list_actions(only_open=True)) == 2

    # 검색: '김과장' 은 '김과장' 담당 업무만
    r = store.search(people=["김과장"])
    assert [a["assignee"] for a in r["actions"]] == ["김과장"]
    assert store.search(keywords=["원가"])["actions"][0]["assignee"] == "박대리"
    assert store.search(date_from=date(2026, 10, 8))["actions"] == []

    # 파일 출력
    paths = pipeline.export(store, mid, tmp_path / "out", ("xlsx", "docx", "pdf"))
    assert all(p.exists() and p.stat().st_size > 1000 for p in paths)
    txt = pipeline.export(store, mid, tmp_path / "out", ("txt",))[0]
    assert txt.name.endswith("_전사문.txt") and "김과장이 업체 3곳" in txt.read_text(encoding="utf-8-sig")
    assert paths[0].name == "2026-10-07_SMT_이전_검토_회의록.xlsx"
    tracker = exporters.export_action_tracker(store.list_actions(), tmp_path / "out", store.action_kpi())
    assert tracker.exists()

    # 회의록 재작성 시 Action Item 교체
    store.update_minutes(mid, {"action_items": [{"assignee": "최부장", "task": "승인"}]})
    assert [a["assignee"] for a in store.get_meeting(mid)["actions"]] == ["최부장"]
    store.delete_meeting(mid)
    assert store.list_actions() == []


def test_load_wav_resamples_to_16k(tmp_path):
    import wave
    import numpy as np
    from meeting_minutes import stt
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.ones((48000, 2)) * 16384).astype(np.int16).tobytes())
    x = stt.load_wav(str(p))
    assert x.dtype == np.float32 and len(x) == 16000 and abs(float(x.mean()) - 0.5) < 1e-3


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _FakeModel:
    """VAD 를 켜면 아무것도 못 찾고, 끄면 문장을 돌려주는 모델 (작은 목소리 상황 재현)."""
    def __init__(self, device="cpu", fail=False):
        self._mm_device, self.fail, self.calls = device, fail, []

    def transcribe(self, audio, **kw):
        self.calls.append((kw.get("vad_filter", False), float(abs(audio).max())))
        if self.fail:
            raise RuntimeError("Library cublas64_12.dll is not found")
        segs = [] if kw.get("vad_filter") else [_Seg(0.5, 2.0, " 김과장 견적 부탁합니다")]
        return iter(segs), None


def _wav(path, amp, seconds=3):
    import wave
    import numpy as np
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        t = np.arange(16000 * seconds)
        w.writeframes((np.sin(t * 0.05) * amp).astype(np.int16).tobytes())


def test_transcribe_quiet_audio_boost_and_vad_fallback(tmp_path, monkeypatch):
    from meeting_minutes import stt
    m = _FakeModel()
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: m)
    _wav(tmp_path / "q.wav", amp=800)  # 약 -32 dB: 작은 목소리
    logs = []
    text, dur = stt.transcribe(str(tmp_path / "q.wav"), progress=lambda p, l: None, log=logs.append)
    assert text == "[00:00:00] 김과장 견적 부탁합니다" and dur == 3.0
    assert [c[0] for c in m.calls] == [True, False]  # VAD → 재시도(무 VAD)
    assert m.calls[0][1] > 0.7  # 음량 보정됨 (0.024 → 최대 30배)
    assert any("배 키워서" in s for s in logs) and any("다시 인식" in s for s in logs)


def test_transcribe_gpu_failure_falls_back_to_cpu(tmp_path, monkeypatch):
    from meeting_minutes import stt
    gpu, cpu = _FakeModel("cuda", fail=True), _FakeModel("cpu")
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: cpu if force_cpu else gpu)
    _wav(tmp_path / "a.wav", amp=20000)
    logs = []
    text, _ = stt.transcribe(str(tmp_path / "a.wav"), log=logs.append)
    assert "김과장" in text and any("CPU 로 다시" in s for s in logs)


def test_transcribe_silent_file_raises(tmp_path, monkeypatch):
    from meeting_minutes import stt
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: _FakeModel())
    _wav(tmp_path / "s.wav", amp=0)
    with pytest.raises(RuntimeError, match="소리가 거의 없습니다"):
        stt.transcribe(str(tmp_path / "s.wav"))


class _ChunkModel:
    """조각마다 '구간N' 한 문장을 돌려주는 가짜 모델 (조각 길이 기록)."""
    _mm_device = "cpu"

    def __init__(self):
        self.lengths = []

    def transcribe(self, audio, **kw):
        self.lengths.append(len(audio) / 16000)
        return iter([_Seg(0.2, 1.0, f"구간{len(self.lengths)}")]), None


def _feed(lt, audio):
    for i in range(0, len(audio), 1600):  # 0.1초 블록 (녹음 콜백과 동일)
        lt.q.put(audio[i:i + 1600].astype(np.int16).tobytes())


def _tone(sec, amp=8000):
    t = np.arange(int(16000 * sec))
    return (np.sin(t * 0.07) * amp).astype(np.int16)


def _silence(sec):
    return np.zeros(int(16000 * sec), dtype=np.int16)


def _ts_sec(line):
    h, m, sec = line[1:9].split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def test_live_cuts_at_pauses_with_file_timeline(monkeypatch):
    from meeting_minutes import live, stt
    m = _ChunkModel()
    monkeypatch.setattr(stt, "_cpu_only", False)
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: m)
    got = []
    lt = live.LiveTranscriber(16000, "small", "p", on_final=lambda cid, ln: got.append(ln))
    lt.start()
    # 말 3초 | 쉼 0.6 | 말 2초 | 쉼 0.6 | 말 3초 | 쉼 1초  → 말이 끊길 때마다 3조각
    _feed(lt, np.concatenate([_tone(3), _silence(0.6), _tone(2), _silence(0.6), _tone(3), _silence(1)]))
    lt.finish()
    lt.join(timeout=10)
    assert len(got) == 3 and lt.error is None
    assert [_ts_sec(x) for x in got] == [0, 3, 6]  # 녹음파일 기준 조각 시작 시각 (0, 3.4, 6.0초 — 0.4초 쉼에서 자름)
    assert lt.text() == "\n".join(got)
    assert all(live.MIN_SEC <= x <= live.MAX_SEC for x in m.lengths)


def test_live_long_speech_capped_and_silence_skipped(monkeypatch):
    from meeting_minutes import live, stt
    m = _ChunkModel()
    monkeypatch.setattr(stt, "_cpu_only", False)
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: m)
    lt = live.LiveTranscriber(16000, "small", "p")
    lt.start()
    _feed(lt, np.concatenate([_tone(20), _silence(15)]))  # 쉬지 않고 20초 + 긴 무음
    lt.finish()
    lt.join(timeout=10)
    assert max(m.lengths) <= live.MAX_SEC + 0.01 and sum(m.lengths) >= 19.9
    assert len(m.lengths) == 3  # 8 + 8 + 4초, 무음 조각은 인식하지 않음
    assert lt.last_proc_sec >= 0


def test_settings_migrate_live_model_to_base(tmp_path, monkeypatch):
    import json
    from meeting_minutes import config
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    assert config.load()["live_model"] == "base"  # 새 설치
    (tmp_path / "settings.json").write_text(json.dumps({"live_model": "small"}), encoding="utf-8")
    assert config.load()["live_model"] == "base"  # v1.4 에서 저장된 기본값 → base
    (tmp_path / "settings.json").write_text(json.dumps({"live_model": "small", "settings_rev": 2}), encoding="utf-8")
    assert config.load()["live_model"] == "small"  # 이후 직접 고른 값은 유지


class _FakePreview:
    """0.1초마다 글자가 하나씩 늘어나는 연속 미리보기 (스트리밍 엔진 흉내)."""
    def __init__(self):
        self.n, self.k = 0, 0

    def accept(self, x):
        if len(x) and np.abs(x).max() > 0.01:
            self.n += 1
        return self.partial()

    def partial(self):
        return "말" * self.n

    def cut(self, final=False):
        self.k += 1
        text, self.n = (f"미리보기{self.k}" if self.n else ""), 0
        return text, final


def _run_live(monkeypatch, model, audio):
    from meeting_minutes import live, stt
    monkeypatch.setattr(stt, "_cpu_only", False)
    monkeypatch.setattr(stt, "_load_model", lambda size, force_cpu=False: model)
    ev = []
    lt = live.LiveTranscriber(16000, "base", "p", preview_factory=lambda note: _FakePreview(),
                              on_partial=lambda t: ev.append(("partial", t)),
                              on_pending=lambda c, ln: ev.append(("pending", c, ln)),
                              on_final=lambda c, ln: ev.append(("final", c, ln)))
    lt.start()
    time.sleep(0.2)  # 미리보기 준비 (실제로는 백그라운드 다운로드·로드)
    _feed(lt, audio)
    lt.finish()
    lt.join(timeout=10)
    return lt, ev


def test_live_preview_then_whisper_replaces(monkeypatch):
    class Slow(_ChunkModel):  # 실제 Whisper 처럼 미리보기보다 늦게 확정
        def transcribe(self, audio, **kw):
            time.sleep(0.3)
            return super().transcribe(audio, **kw)
    audio = np.concatenate([_tone(3), _silence(0.6), _tone(3), _silence(1)])
    lt, ev = _run_live(monkeypatch, Slow(), audio)
    partials = [e[1] for e in ev if e[0] == "partial" and e[1]]
    assert partials and max(len(p) for p in partials) >= 20  # 말하는 동안 글자가 계속 늘어남
    pend = [e for e in ev if e[0] == "pending"]
    fin = [e for e in ev if e[0] == "final"]
    assert [e[2][11:] for e in pend] == ["미리보기1", "미리보기2"]
    assert [e[2][11:] for e in fin] == ["구간1", "구간2"]          # Whisper 확정 문장으로 교체
    assert [e[1] for e in pend] == [e[1] for e in fin]           # 같은 조각 id
    assert all(ev.index(p) < ev.index(f) for p, f in zip(pend, fin))  # 미리보기가 먼저
    assert lt.text().splitlines() == [e[2] for e in fin]


def test_live_whisper_failure_keeps_preview_text(monkeypatch):
    class Broken:
        _mm_device = "cpu"
        def transcribe(self, audio, **kw):
            raise ValueError("boom")
    audio = np.concatenate([_tone(3), _silence(1)])
    lt, ev = _run_live(monkeypatch, Broken(), audio)
    assert lt.text() == "[00:00:00] 미리보기1" and lt.error is None


def test_google_live_relay(monkeypatch):
    """Chrome 페이지 → 127.0.0.1 서버 → 자막 (페이지 동작을 HTTP 요청으로 흉내)."""
    import json
    import urllib.request
    from meeting_minutes import webspeech
    srv = webspeech.GoogleSpeechServer()
    monkeypatch.setattr(srv, "open_browser", lambda: "test")
    t = [10.0]
    ev = []
    live = webspeech.GoogleLive(srv, elapsed=lambda: t[0], on_final=lambda c, ln: ev.append(("final", ln)),
                                on_partial=lambda s: ev.append(("partial", s)), on_status=lambda s: None)
    live.start()
    try:
        def get(path):
            return urllib.request.urlopen(srv.url.rstrip("/") + path, timeout=5).read()

        def post(**body):
            req = urllib.request.Request(srv.url + "event", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)

        assert "webkitSpeechRecognition" in get("/").decode()
        state = json.loads(get("/state"))
        assert state["active"] and srv.page_connected
        s = state["session"]
        post(type="partial", text="SMT 이전은", session=s)
        post(type="partial", text="SMT 이전은 11월까지", session=s)
        post(type="final", text="SMT 이전은 11월까지 검토", session=s)
        post(type="final", text="이전 녹음의 늦은 결과", session=s - 1)  # 이전 세션 이벤트는 무시
        t[0] = 20.0
        post(type="partial", text="김과장 견적", session=s)  # 확정 전에 녹음 종료
        t[0] = 0.0  # 녹음 종료 후 녹음 시각은 0 으로 초기화됨
        live.finish()
        live.join(timeout=5)
        assert not json.loads(get("/state"))["active"]
    finally:
        srv.stop()
    assert live.lines == ["[00:00:09] SMT 이전은 11월까지 검토", "[00:00:19] 김과장 견적"]
    assert ("partial", "SMT 이전은") in ev and live.error is None
