"""Inspection history + activity audit log (SQLite).

Why SQLite and not JSON: these tables GROW without bound (one row per inspection,
one per action). A database gives us cheap appends, crash-safe transactional
writes, and real queries ("today's count", "count per model") — all things a JSON
file is bad at. It's a single file, stdlib `sqlite3`, no server.

Two tables:
  inspections — one row per Run Inspection (feeds the dashboard blocks)
  audit_log   — one row per notable action: who did what, when (accountability)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .paths import ROOT                         # frozen-aware (PyInstaller safe)

DB_PATH = ROOT / "history.db"


def _connect() -> sqlite3.Connection:
    """Open the DB, creating the tables on first use. Returns: sqlite3.Connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inspections (
            id         INTEGER PRIMARY KEY,
            ts         TEXT NOT NULL,   -- ISO timestamp of the inspection
            model      TEXT,            -- recognized model name ('' when NA)
            diameter   REAL,            -- mm, as sent to the PLC
            height     REAL,            -- mm, as sent to the PLC
            confidence REAL,            -- 0..1 recognition confidence
            matched    INTEGER          -- 1 = recognized, 0 = not a known model
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id       INTEGER PRIMARY KEY,
            ts       TEXT NOT NULL,     -- when it happened
            username TEXT,              -- who did it ('' if not logged in)
            action   TEXT,              -- login / login_failed / train_model / ...
            details  TEXT               -- specifics, e.g. "model1 -> typeA"
        )
        """
    )
    conn.commit()
    return conn


def _now() -> str:
    """Current local time as an ISO string (what we store in `ts`)."""
    return datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------- inspections
def record_inspection(model: str, diameter: float, height: float,
                      confidence: float, matched: bool) -> None:
    """Append one inspection row. Returns: None.

    NA inspections are still stored (matched=0, model='') for the audit trail,
    but they never appear in the per-model counts.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO inspections (ts, model, diameter, height, confidence, matched)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), model or "", float(diameter), float(height),
             float(confidence), 1 if matched else 0),
        )


def count_today() -> int:
    """Recognized wheels inspected today. Returns: int."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM inspections"
            " WHERE matched = 1 AND date(ts) = date('now', 'localtime')"
        )
        return int(cur.fetchone()[0])


def count_this_month() -> int:
    """Recognized wheels inspected in the current calendar month. Returns: int."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM inspections"
            " WHERE matched = 1"
            " AND strftime('%Y-%m', ts) = strftime('%Y-%m', 'now', 'localtime')"
        )
        return int(cur.fetchone()[0])


def counts_by_model() -> dict[str, int]:
    """Total recognized inspections per model (NA excluded).

    Returns: {model_name: count}. Models never seen simply won't appear — the UI
    fills them in with 0 from the registry.
    """
    with _connect() as conn:
        cur = conn.execute(
            "SELECT model, COUNT(*) FROM inspections"
            " WHERE matched = 1 AND model <> '' GROUP BY model"
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}


def recent_inspections(limit: int = 20) -> list[tuple[str, str]]:
    """Most recent inspections, newest first.

    Returns: list of (timestamp, model). NA rows come back with model='' so the
    UI can show them as "—".
    """
    with _connect() as conn:
        cur = conn.execute(
            "SELECT ts, model FROM inspections ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [(row[0], row[1] or "") for row in cur.fetchall()]


# ------------------------------------------------------------------ audit log
def log_action(username: str, action: str, details: str = "") -> None:
    """Append one audit row: who did what, when. Returns: None.

    Passwords are never logged — only the username and the action.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, username, action, details) VALUES (?, ?, ?, ?)",
            (_now(), username or "", action, details),
        )


def recent_actions(limit: int = 200) -> list[tuple[str, str, str, str]]:
    """Most recent audit entries, newest first.

    Returns: list of (timestamp, username, action, details).
    """
    with _connect() as conn:
        cur = conn.execute(
            "SELECT ts, username, action, details FROM audit_log"
            " ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [tuple(row) for row in cur.fetchall()]
