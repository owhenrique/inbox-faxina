from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MESSAGE_COLUMNS = (
    "uid, sender, sender_name, domain, subject, internal_date, size, "
    "seen, has_attachment, labels, fetched_at, gm_msgid"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    uid TEXT PRIMARY KEY,
    sender TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    subject TEXT NOT NULL,
    internal_date TEXT NOT NULL,
    size INTEGER NOT NULL,
    seen INTEGER NOT NULL,
    has_attachment INTEGER NOT NULL,
    labels TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    gm_msgid TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manifest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    action TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

INSERT_MESSAGE_SQL = f"""
INSERT INTO messages ({MESSAGE_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(uid) DO UPDATE SET
    sender = excluded.sender,
    sender_name = excluded.sender_name,
    domain = excluded.domain,
    subject = excluded.subject,
    internal_date = excluded.internal_date,
    size = excluded.size,
    seen = excluded.seen,
    has_attachment = excluded.has_attachment,
    labels = excluded.labels,
    fetched_at = excluded.fetched_at,
    gm_msgid = excluded.gm_msgid
"""


@dataclass(frozen=True)
class Message:
    uid: str
    sender: str
    sender_name: str
    domain: str
    subject: str
    internal_date: str
    size: int
    seen: bool
    has_attachment: bool
    labels: tuple[str, ...]
    fetched_at: str
    msgid: str = ""


@dataclass(frozen=True)
class SenderAggregate:
    sender: str
    count: int
    total_size: int
    oldest_date: str


@dataclass(frozen=True)
class Rule:
    id: int
    created_at: str
    action: str
    filter_json: str
    description: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    init_schema(connection)
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
    if "gm_msgid" not in columns:
        connection.execute("ALTER TABLE messages ADD COLUMN gm_msgid TEXT NOT NULL DEFAULT ''")
    connection.commit()


def insert_messages(connection: sqlite3.Connection, messages: Sequence[Message]) -> None:
    rows = [
        (
            message.uid,
            message.sender,
            message.sender_name,
            message.domain,
            message.subject,
            message.internal_date,
            message.size,
            int(message.seen),
            int(message.has_attachment),
            json.dumps(list(message.labels)),
            message.fetched_at,
            message.msgid,
        )
        for message in messages
    ]
    with connection:
        connection.executemany(INSERT_MESSAGE_SQL, rows)


def list_messages(connection: sqlite3.Connection) -> list[Message]:
    rows = connection.execute(
        f"SELECT {MESSAGE_COLUMNS} FROM messages ORDER BY rowid"
    ).fetchall()
    return [
        Message(
            uid=row[0],
            sender=row[1],
            sender_name=row[2],
            domain=row[3],
            subject=row[4],
            internal_date=row[5],
            size=row[6],
            seen=bool(row[7]),
            has_attachment=bool(row[8]),
            labels=tuple(json.loads(row[9])),
            fetched_at=row[10],
            msgid=row[11],
        )
        for row in rows
    ]


def aggregate_by_sender(connection: sqlite3.Connection) -> list[SenderAggregate]:
    rows = connection.execute(
        """
        SELECT sender, COUNT(*), COALESCE(SUM(size), 0), MIN(internal_date)
        FROM messages
        GROUP BY sender
        ORDER BY SUM(size) DESC, sender ASC
        """
    ).fetchall()
    return [
        SenderAggregate(
            sender=row[0],
            count=row[1],
            total_size=row[2],
            oldest_date=row[3],
        )
        for row in rows
    ]


def known_uids(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT uid FROM messages")}


def get_meta(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row[0]


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def add_rule(
    connection: sqlite3.Connection,
    action: str,
    filter_json: str,
    description: str,
) -> int:
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO rules (created_at, action, filter_json, description)
            VALUES (?, ?, ?, ?)
            """,
            (now_iso(), action, filter_json, description),
        )
    return int(cursor.lastrowid)


def list_rules(connection: sqlite3.Connection) -> list[Rule]:
    rows = connection.execute(
        "SELECT id, created_at, action, filter_json, description FROM rules ORDER BY id"
    ).fetchall()
    return [
        Rule(
            id=row[0],
            created_at=row[1],
            action=row[2],
            filter_json=row[3],
            description=row[4],
        )
        for row in rows
    ]


def remove_rule(connection: sqlite3.Connection, rule_id: int) -> None:
    with connection:
        connection.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def record_manifest(
    connection: sqlite3.Connection,
    uid: str,
    action: str,
    batch_id: str,
    status: str,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO manifest (uid, action, batch_id, status, executed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uid, action, batch_id, status, now_iso()),
        )


def succeeded_uids(connection: sqlite3.Connection, action: str) -> set[str]:
    rows = connection.execute(
        "SELECT uid FROM manifest WHERE action = ? AND status = 'ok'", (action,)
    ).fetchall()
    return {row[0] for row in rows}
