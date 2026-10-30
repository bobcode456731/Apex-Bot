"""SQLite persistence with transactional helpers and versioned schema migrations."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .validation import safe_text


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA_VERSION = 1

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS warnings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, archived_at TEXT
);
CREATE INDEX IF NOT EXISTS warnings_active ON warnings(guild_id,user_id,archived_at);
CREATE TABLE IF NOT EXISTS moderation_cases (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, action TEXT NOT NULL,
 actor_id TEXT NOT NULL, target_id TEXT NOT NULL, reason TEXT NOT NULL, outcome TEXT NOT NULL,
 dm_result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locked_channels (
 guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, overwrites_json TEXT NOT NULL,
 actor_id TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(guild_id,channel_id)
);
CREATE TABLE IF NOT EXISTS tickets (
 id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, category TEXT NOT NULL, opener_id TEXT NOT NULL,
 channel_id TEXT UNIQUE, panel_id TEXT, support_role_id TEXT, status TEXT NOT NULL,
 claimant_id TEXT, priority TEXT NOT NULL DEFAULT 'Normal', close_reason TEXT,
 created_at TEXT NOT NULL, closed_at TEXT, transcript_saved INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_ticket_per_category
 ON tickets(guild_id,opener_id,category) WHERE status IN ('creating','open');
CREATE TABLE IF NOT EXISTS ticket_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, kind TEXT NOT NULL,
 actor_id TEXT, target_id TEXT, old_value TEXT, new_value TEXT, detail TEXT,
 created_at TEXT NOT NULL, FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
CREATE TABLE IF NOT EXISTS panel_templates (
 id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, name TEXT NOT NULL, ticket_type TEXT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL, fields_json TEXT NOT NULL,
 colour INTEGER NOT NULL, image_url TEXT, footer TEXT NOT NULL, button_label TEXT NOT NULL,
 button_emoji TEXT, channel_id TEXT NOT NULL, support_role_id TEXT NOT NULL,
 creator_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 published_channel_id TEXT, published_message_id TEXT, published_config_json TEXT NOT NULL DEFAULT '{}',
 state TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS custom_embeds (
 id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, trigger TEXT NOT NULL, aliases_json TEXT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL, fields_json TEXT NOT NULL, colour INTEGER NOT NULL,
 image_url TEXT, footer TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 creator_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS giveaways (
 id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, message_id TEXT,
 host_id TEXT NOT NULL, prize TEXT NOT NULL, winner_count INTEGER NOT NULL,
 created_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL,
 entrants_json TEXT NOT NULL DEFAULT '[]', winners_json TEXT NOT NULL DEFAULT '[]', outcome TEXT
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
 giveaway_id TEXT NOT NULL, user_id TEXT NOT NULL, entered_at TEXT NOT NULL,
 PRIMARY KEY(giveaway_id,user_id), FOREIGN KEY(giveaway_id) REFERENCES giveaways(id)
);
CREATE TABLE IF NOT EXISTS suggestions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 message_id TEXT, submitter_id TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending',
 decision_actor_id TEXT, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestion_votes (
 suggestion_id INTEGER NOT NULL, user_id TEXT NOT NULL, vote INTEGER NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(suggestion_id,user_id),
 FOREIGN KEY(suggestion_id) REFERENCES suggestions(id)
);
CREATE TABLE IF NOT EXISTS suggestion_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, suggestion_id INTEGER NOT NULL, actor_id TEXT NOT NULL,
 old_status TEXT, new_status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(suggestion_id) REFERENCES suggestions(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT, category TEXT NOT NULL, action TEXT NOT NULL,
 actor_id TEXT, target_id TEXT, details_json TEXT NOT NULL, outcome TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_recent ON audit_events(created_at DESC);
"""


class Database:
    """A small synchronous SQLite repository; calls are short and serialized per connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=15000")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                try:
                    self.connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATION_1 + "\nPRAGMA user_version=1;\nCOMMIT;")
                except Exception:
                    self.connection.rollback()
                    raise
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema {version} is newer than this bot supports ({SCHEMA_VERSION}).")

    def close(self) -> None:
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def _db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not initialized.")
        return self.connection

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db().execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db().execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db().execute(sql, params).fetchall())

    @contextmanager
    def transaction(self):
        """Run a short, serialized write transaction and roll back on any failure."""
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._db():
            self._db().execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, utcnow()),
            )

    def audit(self, category: str, action: str, *, guild_id: int | str | None = None,
              actor_id: int | str | None = None, target_id: int | str | None = None,
              details: dict[str, Any] | None = None, outcome: str = "success") -> None:
        def sanitized(value):
            if isinstance(value, dict):
                return {safe_text(str(key), 128): sanitized(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [sanitized(item) for item in value]
            if isinstance(value, str):
                return safe_text(value, 4000)
            return value

        # Callers use IDs and bounded metadata; common credential formats are redacted again here.
        with self._lock, self._db():
            self._db().execute(
                "INSERT INTO audit_events(guild_id,category,action,actor_id,target_id,details_json,outcome,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (str(guild_id) if guild_id is not None else None, category, action,
                 str(actor_id) if actor_id is not None else None,
                 str(target_id) if target_id is not None else None,
                 json.dumps(sanitized(details or {}), ensure_ascii=False), outcome, utcnow()),
            )

    def prune_audit(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - max(1, retention_days) * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._db():
            cursor = self._db().execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff_iso,))
            return cursor.rowcount
