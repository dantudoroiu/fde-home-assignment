"""SQLite persistence (stdlib only).

A new connection per operation keeps this safe to use from FastAPI's threadpool and background tasks
without shared-connection locking. Fine for a single-node demo; Postgres is the scaling path.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    customer_email  TEXT NOT NULL,
    status          TEXT NOT NULL,
    triage_json     TEXT,
    draft_json      TEXT,
    article_ids_json TEXT,
    flags_json      TEXT,
    review_reasons_json TEXT,
    error           TEXT,
    pipeline_ms     INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id          INTEGER,
    step               TEXT NOT NULL,
    model              TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    attempt            INTEGER NOT NULL,
    outcome            TEXT NOT NULL,
    latency_ms         INTEGER NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL,
    stop_reason        TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_ticket ON llm_calls(ticket_id);

CREATE TABLE IF NOT EXISTS feedback (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id          INTEGER NOT NULL REFERENCES tickets(id),
    action             TEXT NOT NULL,
    final_reply        TEXT,
    ai_category        TEXT,
    ai_priority        TEXT,
    corrected_category TEXT,
    corrected_priority TEXT,
    edit_ratio         REAL,
    created_at         TEXT NOT NULL
);
"""

_JSON_FIELDS = ("triage", "draft", "article_ids", "flags", "review_reasons")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    # --- tickets -------------------------------------------------------------------------------

    def create_ticket(self, subject: str, body: str, customer_email: str) -> int:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO tickets (subject, body, customer_email, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'received', ?, ?)",
                (subject, body, customer_email, now, now),
            )
            return int(cur.lastrowid)

    def update_ticket(self, ticket_id: int, **fields: Any) -> None:
        """Update columns; keys in _JSON_FIELDS are serialized to their *_json column."""
        columns: dict[str, Any] = {}
        for key, value in fields.items():
            if key in _JSON_FIELDS:
                columns[f"{key}_json"] = None if value is None else json.dumps(value)
            else:
                columns[key] = value
        columns["updated_at"] = _now()
        assignments = ", ".join(f"{col} = ?" for col in columns)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tickets SET {assignments} WHERE id = ?", (*columns.values(), ticket_id)
            )

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _ticket_from_row(row) if row else None

    def list_tickets(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM tickets"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [_ticket_from_row(r) for r in rows]

    # --- llm calls & feedback ------------------------------------------------------------------

    def record_llm_call(self, call: dict[str, Any]) -> None:
        call = {**call, "created_at": _now()}
        cols = ", ".join(call)
        placeholders = ", ".join("?" for _ in call)
        with self._connect() as conn:
            conn.execute(f"INSERT INTO llm_calls ({cols}) VALUES ({placeholders})", tuple(call.values()))

    def list_llm_calls(self, ticket_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if ticket_id is None:
                rows = conn.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM llm_calls WHERE ticket_id = ? ORDER BY id", (ticket_id,)
                ).fetchall()
        return [dict(r) for r in rows]

    def record_feedback(self, feedback: dict[str, Any]) -> None:
        feedback = {**feedback, "created_at": _now()}
        cols = ", ".join(feedback)
        placeholders = ", ".join("?" for _ in feedback)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO feedback ({cols}) VALUES ({placeholders})", tuple(feedback.values())
            )

    def list_feedback(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY id").fetchall()]

    def ticket_ids_with_status(self, statuses: tuple[str, ...]) -> list[int]:
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM tickets WHERE status IN ({placeholders}) ORDER BY id", statuses
            ).fetchall()
        return [r["id"] for r in rows]

    def status_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def pipeline_latencies_ms(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pipeline_ms FROM tickets WHERE pipeline_ms IS NOT NULL"
            ).fetchall()
        return [r["pipeline_ms"] for r in rows]


def _ticket_from_row(row: sqlite3.Row) -> dict[str, Any]:
    ticket = dict(row)
    for key in _JSON_FIELDS:
        raw = ticket.pop(f"{key}_json")
        ticket[key] = json.loads(raw) if raw else None
    return ticket
