from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from email_cleanup.db import (
    Message,
    Rule,
    SenderAggregate,
    add_rule,
    aggregate_by_sender,
    connect,
    get_meta,
    init_schema,
    insert_messages,
    list_messages,
    list_rules,
    record_manifest,
    remove_rule,
    set_meta,
    succeeded_uids,
)


def make_message(
    uid: str,
    *,
    sender: str = "news@shop.example",
    sender_name: str = "Shop News",
    domain: str = "shop.example",
    subject: str = "Subject",
    internal_date: str = "2024-01-01T10:00:00+00:00",
    size: int = 1024,
    seen: bool = False,
    has_attachment: bool = False,
    labels: tuple[str, ...] = ("\\Inbox",),
    fetched_at: str = "2026-10-15T10:00:00+00:00",
) -> Message:
    return Message(
        uid=uid,
        sender=sender,
        sender_name=sender_name,
        domain=domain,
        subject=subject,
        internal_date=internal_date,
        size=size,
        seen=seen,
        has_attachment=has_attachment,
        labels=labels,
        fetched_at=fetched_at,
    )


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    database = connect(tmp_path / "catalog.sqlite")
    yield database
    database.close()


def test_connect_enables_wal(connection: sqlite3.Connection) -> None:
    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal"


def test_schema_columns(connection: sqlite3.Connection) -> None:
    assert table_columns(connection, "messages") == [
        "uid",
        "sender",
        "sender_name",
        "domain",
        "subject",
        "internal_date",
        "size",
        "seen",
        "has_attachment",
        "labels",
        "fetched_at",
            "gm_msgid",
    ]
    assert table_columns(connection, "rules") == [
        "id",
        "created_at",
        "action",
        "filter_json",
        "description",
    ]
    assert table_columns(connection, "manifest") == [
        "id",
        "uid",
        "action",
        "batch_id",
        "status",
        "executed_at",
    ]
    assert table_columns(connection, "meta") == ["key", "value"]


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    first = connect(path)
    insert_messages(first, [make_message("1")])
    first.close()

    second = connect(path)
    init_schema(second)

    assert [message.uid for message in list_messages(second)] == ["1"]
    second.close()


def test_insert_messages_in_batch_and_read(connection: sqlite3.Connection) -> None:
    messages = [
        make_message(
            "1",
            subject="Promoção imperdível",
            size=2048,
            seen=True,
            has_attachment=True,
            labels=("\\Inbox", "Newsletter"),
        ),
        make_message("2", sender="friend@example.com", sender_name="Friend", domain="example.com"),
        make_message("3", internal_date="2020-03-04T08:30:00+00:00", size=4096),
    ]

    insert_messages(connection, messages)

    assert list_messages(connection) == messages


def test_insert_messages_replaces_existing_uid(connection: sqlite3.Connection) -> None:
    insert_messages(connection, [make_message("1", subject="Before", size=10)])
    insert_messages(connection, [make_message("1", subject="After", size=99)])

    stored = list_messages(connection)

    assert len(stored) == 1
    assert stored[0].subject == "After"
    assert stored[0].size == 99


def test_aggregate_by_sender(connection: sqlite3.Connection) -> None:
    insert_messages(
        connection,
        [
            make_message(
                "1",
                sender="news@shop.example",
                sender_name="Shop News",
                internal_date="2020-01-01T10:00:00+00:00",
                size=3500,
            ),
            make_message(
                "2",
                sender="news@shop.example",
                sender_name="Shop News",
                internal_date="2021-06-01T10:00:00+00:00",
                size=500,
            ),
            make_message(
                "3",
                sender="friend@example.com",
                sender_name="Friend",
                internal_date="2019-05-05T10:00:00+00:00",
                size=5000,
            ),
        ],
    )

    assert aggregate_by_sender(connection) == [
        SenderAggregate(
            sender="friend@example.com",
            count=1,
            total_size=5000,
            oldest_date="2019-05-05T10:00:00+00:00",
        ),
        SenderAggregate(
            sender="news@shop.example",
            count=2,
            total_size=4000,
            oldest_date="2020-01-01T10:00:00+00:00",
        ),
    ]


def test_meta_get_and_set(connection: sqlite3.Connection) -> None:
    assert get_meta(connection, "last_uid") is None

    set_meta(connection, "last_uid", "1234")

    assert get_meta(connection, "last_uid") == "1234"

    set_meta(connection, "last_uid", "9876")

    assert get_meta(connection, "last_uid") == "9876"


def test_add_list_and_remove_rules(connection: sqlite3.Connection) -> None:
    first_id = add_rule(
        connection,
        action="arquivar",
        filter_json='{"sender": "news@shop.example"}',
        description="Arquivar News Shop",
    )
    second_id = add_rule(
        connection,
        action="lixeira",
        filter_json='{"age_years": 5}',
        description="Lixeira: mais de 5 anos",
    )

    rules = list_rules(connection)

    assert [rule.id for rule in rules] == [first_id, second_id]
    first, second = rules
    assert isinstance(first, Rule)
    assert first.action == "arquivar"
    assert first.filter_json == '{"sender": "news@shop.example"}'
    assert first.description == "Arquivar News Shop"
    assert datetime.fromisoformat(first.created_at)
    assert second.action == "lixeira"
    assert second.description == "Lixeira: mais de 5 anos"

    remove_rule(connection, first_id)

    remaining = list_rules(connection)

    assert [rule.id for rule in remaining] == [second_id]
    assert remaining[0].description == "Lixeira: mais de 5 anos"


def test_manifest_ok_uids(connection: sqlite3.Connection) -> None:
    record_manifest(connection, uid="1", action="arquivar", batch_id="batch-1", status="ok")
    record_manifest(connection, uid="2", action="lixeira", batch_id="batch-1", status="ok")
    record_manifest(connection, uid="3", action="arquivar", batch_id="batch-1", status="error")
    record_manifest(connection, uid="4", action="arquivar", batch_id="batch-2", status="ok")

    assert succeeded_uids(connection, "arquivar") == {"1", "4"}
    assert succeeded_uids(connection, "lixeira") == {"2"}
    assert succeeded_uids(connection, "apagar") == set()
