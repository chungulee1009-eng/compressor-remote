"""SQLite 저장소: 회의(meetings) + Action Item(action_items) + 검색."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

STATUSES = ("미착수", "진행중", "완료", "보류")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    attendees    TEXT DEFAULT '',
    location     TEXT DEFAULT '',
    audio_path   TEXT DEFAULT '',
    duration_sec REAL DEFAULT 0,
    transcript   TEXT DEFAULT '',
    minutes_json TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    assignee    TEXT NOT NULL,
    task        TEXT NOT NULL,
    due_text    TEXT DEFAULT '',
    due_date    TEXT DEFAULT '',
    priority    TEXT DEFAULT '보통',
    status      TEXT DEFAULT '미착수',
    note        TEXT DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_assignee ON action_items(assignee);
CREATE INDEX IF NOT EXISTS ix_ai_status ON action_items(status);
CREATE INDEX IF NOT EXISTS ix_m_date ON meetings(meeting_date);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def display_status(status: str, due_date: str, today: date | None = None) -> str:
    """완료 안 됐는데 기한이 지났으면 '지연' 으로 표시."""
    today = today or date.today()
    if status not in ("완료", "보류") and due_date:
        try:
            if date.fromisoformat(due_date) < today:
                return "지연"
        except ValueError:
            pass
    return status


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------ 회의
    def save_meeting(self, title: str, meeting_date: date, attendees: str, location: str, audio_path: str,
                     duration_sec: float, transcript: str, minutes: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO meetings(title, meeting_date, attendees, location, audio_path, duration_sec, transcript,"
                " minutes_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (title, meeting_date.isoformat(), attendees, location, audio_path, duration_sec, transcript,
                 json.dumps(minutes, ensure_ascii=False), _now()),
            )
            mid = cur.lastrowid
            self._insert_actions(mid, minutes.get("action_items", []))
        return mid

    def _insert_actions(self, meeting_id: int, items: list[dict]) -> None:
        self.conn.executemany(
            "INSERT INTO action_items(meeting_id, assignee, task, due_text, due_date, priority, status, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(meeting_id, a.get("assignee", "미정"), a.get("task", ""), a.get("due_text", ""), a.get("due_date", ""),
              a.get("priority", "보통"), "미착수", _now()) for a in items if a.get("task")],
        )

    def update_minutes(self, meeting_id: int, minutes: dict, replace_actions: bool = True) -> None:
        with self.conn:
            self.conn.execute("UPDATE meetings SET minutes_json=? WHERE id=?",
                              (json.dumps(minutes, ensure_ascii=False), meeting_id))
            if replace_actions:
                self.conn.execute("DELETE FROM action_items WHERE meeting_id=?", (meeting_id,))
                self._insert_actions(meeting_id, minutes.get("action_items", []))

    def get_meeting(self, meeting_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["minutes"] = json.loads(d.pop("minutes_json") or "{}")
        d["actions"] = self.list_actions(meeting_id=meeting_id)
        return d

    def list_meetings(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT m.id, m.title, m.meeting_date, m.attendees, m.duration_sec,"
            " (SELECT COUNT(*) FROM action_items a WHERE a.meeting_id=m.id) AS n_actions,"
            " (SELECT COUNT(*) FROM action_items a WHERE a.meeting_id=m.id AND a.status!='완료') AS n_open"
            " FROM meetings m ORDER BY m.meeting_date DESC, m.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete_meeting(self, meeting_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))

    # ------------------------------------------------------------ Action Item
    def list_actions(self, meeting_id: int | None = None, assignee: str = "", only_open: bool = False) -> list[dict]:
        sql = ("SELECT a.*, m.title AS meeting_title, m.meeting_date FROM action_items a"
               " JOIN meetings m ON m.id = a.meeting_id WHERE 1=1")
        args: list = []
        if meeting_id is not None:
            sql += " AND a.meeting_id=?"
            args.append(meeting_id)
        if assignee:
            sql += " AND a.assignee LIKE ?"
            args.append(f"%{assignee}%")
        if only_open:
            sql += " AND a.status NOT IN ('완료','보류')"
        sql += " ORDER BY CASE WHEN a.due_date='' THEN 1 ELSE 0 END, a.due_date, a.id"
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["display_status"] = display_status(d["status"], d["due_date"])
            out.append(d)
        return out

    def set_action_status(self, action_id: int, status: str, note: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        with self.conn:
            if note is None:
                self.conn.execute("UPDATE action_items SET status=?, updated_at=? WHERE id=?", (status, _now(), action_id))
            else:
                self.conn.execute("UPDATE action_items SET status=?, note=?, updated_at=? WHERE id=?",
                                  (status, note, _now(), action_id))

    def update_action(self, action_id: int, **fields) -> None:
        allowed = {"assignee", "task", "due_text", "due_date", "priority", "status", "note"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        with self.conn:
            self.conn.execute(
                f"UPDATE action_items SET {', '.join(k + '=?' for k in sets)}, updated_at=? WHERE id=?",
                (*sets.values(), _now(), action_id))

    def action_kpi(self, today: date | None = None) -> dict:
        acts = self.list_actions()
        today = today or date.today()
        k = {"전체": len(acts), "완료": 0, "진행중": 0, "미착수": 0, "보류": 0, "지연": 0}
        for a in acts:
            k[display_status(a["status"], a["due_date"], today)] += 1
        k["완료율"] = round(k["완료"] / k["전체"] * 100, 1) if k["전체"] else 0.0
        return k

    # ------------------------------------------------------------ 검색
    def search(self, people: list[str] | None = None, keywords: list[str] | None = None,
               date_from: date | None = None, date_to: date | None = None) -> dict:
        """회의(전사문/회의록) + Action Item 을 조건으로 검색."""
        people = people or []
        keywords = keywords or []
        # Action Item
        sql = ("SELECT a.*, m.title AS meeting_title, m.meeting_date FROM action_items a"
               " JOIN meetings m ON m.id=a.meeting_id WHERE 1=1")
        args: list = []
        if date_from:
            sql += " AND m.meeting_date >= ?"
            args.append(date_from.isoformat())
        if date_to:
            sql += " AND m.meeting_date <= ?"
            args.append(date_to.isoformat())
        if people:
            # '김과장' 은 '김철수과장' 에도 맞도록 성+직급으로 비교
            sql += " AND (" + " OR ".join("(a.assignee LIKE ? AND a.assignee LIKE ?)" for _ in people) + ")"
            for p in people:
                args += [f"{p[0]}%", f"%{_title_of(p)}"]
        for k in keywords:
            sql += " AND (a.task LIKE ? OR m.title LIKE ? OR a.note LIKE ?)"
            args += [f"%{k}%"] * 3
        sql += " ORDER BY m.meeting_date DESC, a.id"
        actions = [dict(r) | {"display_status": display_status(r["status"], r["due_date"])}
                   for r in self.conn.execute(sql, args).fetchall()]

        # 회의
        msql = "SELECT id, title, meeting_date, attendees, transcript, minutes_json FROM meetings WHERE 1=1"
        margs: list = []
        if date_from:
            msql += " AND meeting_date >= ?"
            margs.append(date_from.isoformat())
        if date_to:
            msql += " AND meeting_date <= ?"
            margs.append(date_to.isoformat())
        for k in keywords + [_title_of(p) for p in people]:
            msql += " AND (title LIKE ? OR transcript LIKE ? OR minutes_json LIKE ?)"
            margs += [f"%{k}%"] * 3
        msql += " ORDER BY meeting_date DESC LIMIT 50"
        meetings = []
        for r in self.conn.execute(msql, margs).fetchall():
            snippet = _snippet(r["transcript"], keywords + people)
            meetings.append({"id": r["id"], "title": r["title"], "meeting_date": r["meeting_date"],
                             "attendees": r["attendees"], "snippet": snippet})
        return {"actions": actions, "meetings": meetings}


def _title_of(person: str) -> str:
    from .rules import TITLES
    for t in sorted(TITLES.split("|"), key=len, reverse=True):
        if person.endswith(t):
            return t
    return person


def _snippet(text: str, terms: list[str], width: int = 60) -> str:
    for t in terms:
        i = text.find(t)
        if i >= 0:
            return "…" + text[max(0, i - width): i + width].replace("\n", " ") + "…"
    return text[:width * 2].replace("\n", " ")
