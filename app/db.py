"""SQLite 持久化层：保存追迹结果与各段信息。"""
from __future__ import annotations

import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "traces.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    start_x    REAL NOT NULL,
    start_y    REAL NOT NULL,
    dir_x      REAL NOT NULL,
    dir_y      REAL NOT NULL,
    n_start    REAL NOT NULL,
    status     TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   INTEGER NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    seg_index  INTEGER NOT NULL,
    origin_x   REAL NOT NULL,
    origin_y   REAL NOT NULL,
    dir_x      REAL NOT NULL,
    dir_y      REAL NOT NULL,
    n_medium   REAL NOT NULL,
    hit_x      REAL,
    hit_y      REAL,
    event      TEXT,
    n_after    REAL
);

CREATE INDEX IF NOT EXISTS idx_segments_trace ON segments(trace_id, seg_index);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建库建表（幂等），服务启动时调用。"""
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def insert_trace(
    start: tuple[float, float],
    direction: tuple[float, float],
    n_start: float,
    status: str,
    reason: str | None,
    segments: list,
) -> int:
    """写入一条追迹记录及其所有分段，返回 trace id。"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO traces (start_x, start_y, dir_x, dir_y, n_start, status, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (start[0], start[1], direction[0], direction[1], n_start, status, reason),
        )
        trace_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO segments (trace_id, seg_index, origin_x, origin_y, dir_x, dir_y,"
            " n_medium, hit_x, hit_y, event, n_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    trace_id,
                    i,
                    s.origin[0],
                    s.origin[1],
                    s.direction[0],
                    s.direction[1],
                    s.n_medium,
                    s.hit_point[0] if s.hit_point else None,
                    s.hit_point[1] if s.hit_point else None,
                    s.event,
                    s.n_after,
                )
                for i, s in enumerate(segments)
            ],
        )
    return trace_id


def _row_to_segment(row: sqlite3.Row) -> dict:
    return {
        "seg_index": row["seg_index"],
        "origin": {"x": row["origin_x"], "y": row["origin_y"]},
        "direction": {"x": row["dir_x"], "y": row["dir_y"]},
        "n_medium": row["n_medium"],
        "hit_point": None
        if row["hit_x"] is None
        else {"x": row["hit_x"], "y": row["hit_y"]},
        "event": row["event"],
        "n_after": row["n_after"],
    }


def _row_to_trace(row: sqlite3.Row, segments: list[dict]) -> dict:
    return {
        "id": row["id"],
        "start": {"x": row["start_x"], "y": row["start_y"]},
        "direction": {"x": row["dir_x"], "y": row["dir_y"]},
        "n_start": row["n_start"],
        "status": row["status"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "segments": segments,
    }


def get_trace(trace_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
        if row is None:
            return None
        seg_rows = conn.execute(
            "SELECT * FROM segments WHERE trace_id = ? ORDER BY seg_index", (trace_id,)
        ).fetchall()
    return _row_to_trace(row, [_row_to_segment(r) for r in seg_rows])


def get_segment(trace_id: int, seg_index: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM segments WHERE trace_id = ? AND seg_index = ?",
            (trace_id, seg_index),
        ).fetchone()
    return _row_to_segment(row) if row else None


def list_traces() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, reason, n_start, created_at FROM traces ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]
