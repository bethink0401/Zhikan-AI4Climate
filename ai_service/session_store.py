"""Persistent SQLite session storage with TTL and optimistic concurrency."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class SessionConflictError(RuntimeError):
    """The persisted session changed after it was read."""


@dataclass(slots=True)
class StoredSession:
    session_id: str
    task_context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    client_messages: list[dict[str, Any]] = field(default_factory=list)
    canonical_agent_results: dict[str, Any] | None = None
    evidence_metadata: list[dict[str, Any]] = field(default_factory=list)
    last_user_goal: str = "unknown"
    last_audience: str = "unknown"
    last_route: str | None = None
    analysis_generated_at: str | None = None
    analysis_source: str | None = None
    # Persisted phase-3A visualization state.  The canonical result remains
    # the source of data; this field stores only the validated semantic view.
    visualization_context: dict[str, Any] = field(default_factory=dict)
    # Server-validated retrieval metadata; never an authenticated identity.
    knowledge_context: dict[str, Any] | None = None
    # Phase 5: last recommendation result for multi-turn explanation
    last_recommendation: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    version: int = 0

    def state_payload(self) -> dict[str, Any]:
        return {
            "task_context": self.task_context,
            "history": self.history,
            "client_messages": self.client_messages,
            "canonical_agent_results": self.canonical_agent_results,
            "evidence_metadata": self.evidence_metadata,
            "last_user_goal": self.last_user_goal,
            "last_audience": self.last_audience,
            "last_route": self.last_route,
            "analysis_generated_at": self.analysis_generated_at,
            "analysis_source": self.analysis_source,
            "visualization_context": self.visualization_context,
            "knowledge_context": self.knowledge_context,
            "last_recommendation": self.last_recommendation,
        }


class SQLiteSessionStore:
    """State is stored in SQLite; in-process memory is never authoritative."""

    def __init__(self, path: str | Path, *, ttl_seconds: int = 86_400, clock: Clock = utc_now) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_sessions (
                    session_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_sessions_expires_at ON ai_sessions(expires_at)"
            )

    def get(self, session_id: str) -> StoredSession | None:
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_sessions WHERE session_id = ?", (normalized,)
            ).fetchone()
            if row is None:
                return None
            if _parse(row["expires_at"]) <= self.clock():
                connection.execute("DELETE FROM ai_sessions WHERE session_id = ?", (normalized,))
                return None
            return self._from_row(row)

    def get_or_create(self, session_id: str) -> StoredSession:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        now = self.clock()
        normalized = session_id.strip()
        session = StoredSession(
            session_id=normalized,
            created_at=_iso(now),
            updated_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=self.ttl_seconds)),
            version=0,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO ai_sessions(session_id, version, state_json, created_at, updated_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        normalized,
                        0,
                        json.dumps(session.state_payload(), ensure_ascii=False, separators=(",", ":")),
                        session.created_at,
                        session.updated_at,
                        session.expires_at,
                    ),
                )
        except sqlite3.IntegrityError:
            concurrent = self.get(normalized)
            if concurrent is None:
                raise SessionConflictError("session was concurrently created and expired")
            return concurrent
        return session

    def save(self, session: StoredSession, *, expected_version: int) -> StoredSession:
        if session.version != expected_version:
            raise SessionConflictError("in-memory session version does not match expected_version")
        now = self.clock()
        next_version = expected_version + 1
        updated_at = _iso(now)
        expires_at = _iso(now + timedelta(seconds=self.ttl_seconds))
        payload = json.dumps(session.state_payload(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE ai_sessions SET version = ?, state_json = ?, updated_at = ?, expires_at = ? "
                "WHERE session_id = ? AND version = ?",
                (next_version, payload, updated_at, expires_at, session.session_id, expected_version),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise SessionConflictError("session version conflict")
            connection.execute("COMMIT")
        session.version = next_version
        session.updated_at = updated_at
        session.expires_at = expires_at
        return session

    def delete(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM ai_sessions WHERE session_id = ?", (session_id.strip(),))

    def purge_expired(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM ai_sessions WHERE expires_at <= ?", (_iso(self.clock()),)
            )
            return cursor.rowcount

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StoredSession:
        state = json.loads(row["state_json"])
        return StoredSession(
            session_id=row["session_id"],
            task_context=state.get("task_context") or {},
            history=state.get("history") or [],
            client_messages=state.get("client_messages") or [],
            canonical_agent_results=state.get("canonical_agent_results"),
            evidence_metadata=state.get("evidence_metadata") or [],
            last_user_goal=state.get("last_user_goal") or "unknown",
            last_audience=state.get("last_audience") or "unknown",
            last_route=state.get("last_route"),
            analysis_generated_at=state.get("analysis_generated_at"),
            analysis_source=state.get("analysis_source"),
            visualization_context=state.get("visualization_context") or {},
            knowledge_context=state.get("knowledge_context"),
            last_recommendation=state.get("last_recommendation"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            version=int(row["version"]),
        )
