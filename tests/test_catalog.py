from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from email_cleanup.catalog import catalog_messages
from email_cleanup.imap import ImapError
from email_cleanup.db import connect, get_meta, list_messages

from fake_imap import ATTACHMENT_BODYSTRUCTURE, FakeIMAP4_SSL, RecordedCommand

ENCODED_SUBJECT = "=?UTF-8?B?UHJvbW/Dp8OjbyBpbXBlcmTDrXZlbA==?="
BROKEN_CHARSET_SUBJECT = "=?X-UNKNOWN?B?T2xkIFN1YmplY3Q=?="
FETCH_ITEMS = frozenset(
    {"RFC822.SIZE", "INTERNALDATE", "FLAGS", "ENVELOPE", "BODYSTRUCTURE", "X-GM-LABELS", "X-GM-MSGID"}
)


def parse_uid_set(spec: str) -> set[str]:
    uids: set[str] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            start_text, _, end_text = token.partition(":")
            low, high = sorted((int(start_text), int(end_text)))
            uids.update(str(uid) for uid in range(low, high + 1))
            continue
        uids.add(token)
    return uids


def make_database(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "catalog.sqlite")


def make_inbox(count: int, *, start: int = 1) -> FakeIMAP4_SSL:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    for uid in range(start, start + count):
        fake.add_message(str(uid), sender_email="news@shop.example")
    return fake


def fetch_uid_sets(fake: FakeIMAP4_SSL) -> list[set[str]]:
    return [parse_uid_set(call.args[1]) for call in fake.uid_calls("FETCH")]


def fetch_items(call: RecordedCommand) -> set[str]:
    return set(call.args[2].strip("()").upper().split())


def test_catalog_parses_and_stores_messages(tmp_path: Path) -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_message(
        "1",
        size=2048,
        internaldate="01-Jan-2024 09:00:00 +0000",
        flags=["\\Seen"],
        subject=ENCODED_SUBJECT,
        sender_name="Loja",
        sender_email="News@Shop.Example",
        labels=["\\Inbox", "Promoções"],
    )
    fake.add_message(
        "2",
        size=1024,
        internaldate="05-Mar-2021 22:15:30 +0000",
        subject="Reunião de equipe",
        sender_name="Ana",
        sender_email="ana@example.com",
    )
    fake.add_message(
        "3",
        size=4096,
        internaldate="31-Dec-2019 23:59:59 +0000",
        flags=["\\Seen"],
        subject=BROKEN_CHARSET_SUBJECT,
        sender_name="Bot",
        sender_email="bot@alerts.example",
        labels=["\\Inbox"],
        bodystructure=ATTACHMENT_BODYSTRUCTURE,
    )
    database = make_database(tmp_path)

    cataloged = catalog_messages(fake, database)

    assert cataloged == 3
    stored = {message.uid: message for message in list_messages(database)}
    assert sorted(stored, key=int) == ["1", "2", "3"]

    first = stored["1"]
    assert first.sender == "news@shop.example"
    assert first.sender_name == "Loja"
    assert first.domain == "shop.example"
    assert first.subject == "Promoção imperdível"
    assert datetime.fromisoformat(first.internal_date) == datetime(2024, 1, 1, 9, tzinfo=timezone.utc)
    assert first.size == 2048
    assert first.seen is True
    assert first.has_attachment is False
    assert set(first.labels) == {"\\Inbox", "Promoções"}
    assert datetime.fromisoformat(first.fetched_at)

    second = stored["2"]
    assert second.sender == "ana@example.com"
    assert second.sender_name == "Ana"
    assert second.domain == "example.com"
    assert second.subject == "Reunião de equipe"
    assert datetime.fromisoformat(second.internal_date) == datetime(
        2021, 3, 5, 22, 15, 30, tzinfo=timezone.utc
    )
    assert second.size == 1024
    assert second.seen is False
    assert second.has_attachment is False
    assert tuple(second.labels) == ()

    third = stored["3"]
    assert third.sender == "bot@alerts.example"
    assert third.subject == "Old Subject"
    assert third.seen is True
    assert third.has_attachment is True
    assert tuple(third.labels) == ("\\Inbox",)

    assert fake.commands("examine")[0].args == ("INBOX",)
    assert fake.commands("select") == []
    assert fake.uid_calls("SEARCH")
    assert fetch_uid_sets(fake) == [{"1", "2", "3"}]
    for call in fake.uid_calls("FETCH"):
        assert FETCH_ITEMS <= fetch_items(call)
    assert fake.uid_calls("STORE") == []
    assert fake.uid_calls("MOVE") == []
    assert fake.uid_calls("EXPUNGE") == []


def test_catalog_fetches_in_batches_of_500(tmp_path: Path) -> None:
    fake = make_inbox(1200)
    database = make_database(tmp_path)

    cataloged = catalog_messages(fake, database)

    assert cataloged == 1200
    calls = fake.uid_calls("FETCH")
    assert len(calls) == 3
    assert sorted(len(parse_uid_set(call.args[1])) for call in calls) == [200, 500, 500]
    assert len(list_messages(database)) == 1200


def test_labels_missing_when_gmail_capability_is_unavailable(tmp_path: Path) -> None:
    fake = make_inbox(2)
    fake.capabilities.discard("X-GM-EXT-1")
    database = make_database(tmp_path)

    cataloged = catalog_messages(fake, database)

    assert cataloged == 2
    assert [tuple(message.labels) for message in list_messages(database)] == [(), ()]


def test_resume_fetches_only_uids_after_last_uid(tmp_path: Path) -> None:
    fake = make_inbox(3)
    database = make_database(tmp_path)
    catalog_messages(fake, database)
    fake.add_message("4", sender_email="news@shop.example")
    fake.add_message("5", sender_email="news@shop.example")
    before = len(fake.uid_calls("FETCH"))

    cataloged = catalog_messages(fake, database)

    assert cataloged == 2
    new_sets = [parse_uid_set(call.args[1]) for call in fake.uid_calls("FETCH")[before:]]
    assert new_sets == [{"4", "5"}]
    assert get_meta(database, "last_uid") == "5"
    assert sorted((message.uid for message in list_messages(database)), key=int) == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]


def test_refresh_refetches_everything(tmp_path: Path) -> None:
    fake = make_inbox(4)
    database = make_database(tmp_path)
    catalog_messages(fake, database)
    before = len(fake.uid_calls("FETCH"))

    cataloged = catalog_messages(fake, database, refresh=True)

    assert cataloged == 4
    new_sets = [parse_uid_set(call.args[1]) for call in fake.uid_calls("FETCH")[before:]]
    assert new_sets == [{"1", "2", "3", "4"}]
    assert get_meta(database, "last_uid") == "4"


def test_limit_caps_cataloged_messages_and_last_uid(tmp_path: Path) -> None:
    fake = make_inbox(7)
    database = make_database(tmp_path)
    progress: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress.append((done, total))

    cataloged = catalog_messages(fake, database, limit=3, on_progress=on_progress)

    assert cataloged == 3
    assert sorted((message.uid for message in list_messages(database)), key=int) == ["1", "2", "3"]
    assert get_meta(database, "last_uid") == "3"
    assert progress[-1] == (3, 3)

    before = len(fake.uid_calls("FETCH"))
    cataloged = catalog_messages(fake, database)

    assert cataloged == 4
    new_sets = [parse_uid_set(call.args[1]) for call in fake.uid_calls("FETCH")[before:]]
    assert new_sets == [{"4", "5", "6", "7"}]
    assert get_meta(database, "last_uid") == "7"


def test_interrupt_keeps_committed_batches_and_resumes(tmp_path: Path) -> None:
    fake = make_inbox(6)
    path = tmp_path / "catalog.sqlite"
    database = connect(path)
    calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        calls.append((done, total))
        if done >= 4:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        catalog_messages(fake, database, batch_size=2, on_progress=on_progress)

    assert calls == [(2, 6), (4, 6)]
    reader = connect(path)
    assert sorted((message.uid for message in list_messages(reader)), key=int) == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert get_meta(reader, "last_uid") == "4"
    reader.close()
    assert fetch_uid_sets(fake) == [{"1", "2"}, {"3", "4"}]

    cataloged = catalog_messages(fake, database)

    assert cataloged == 2
    assert get_meta(database, "last_uid") == "6"
    assert sorted((message.uid for message in list_messages(database)), key=int) == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    ]


def test_progress_reports_done_and_total_per_batch(tmp_path: Path) -> None:
    fake = make_inbox(5)
    database = make_database(tmp_path)
    progress: list[tuple[int, int]] = []

    catalog_messages(fake, database, batch_size=2, on_progress=lambda done, total: progress.append((done, total)))

    assert progress[-1] == (5, 5)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)
    assert all(total == 5 for _, total in progress)


def test_empty_inbox_catalogs_nothing(tmp_path: Path) -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    database = make_database(tmp_path)

    cataloged = catalog_messages(fake, database)

    assert cataloged == 0
    assert list_messages(database) == []
    assert fake.uid_calls("FETCH") == []


def test_limit_runs_never_skip_uids_missing_below_last_uid(tmp_path: Path) -> None:
    fake = make_inbox(4)
    database = make_database(tmp_path)
    catalog_messages(fake, database, limit=2)
    fake.add_message("3", sender_email="late@shop.example")
    database.execute("DELETE FROM messages WHERE uid = '1'")
    database.commit()

    catalog_messages(fake, database)

    assert {message.uid for message in list_messages(database)} == {"1", "2", "3", "4"}


def test_catalog_records_account_and_finished_at(tmp_path: Path) -> None:
    fake = make_inbox(3)
    database = make_database(tmp_path)

    catalog_messages(fake, database, limit=1, account="ana@gmail.com")

    assert get_meta(database, "account") == "ana@gmail.com"
    assert get_meta(database, "catalog_finished_at") is None

    catalog_messages(fake, database, account="ana@gmail.com")

    assert get_meta(database, "catalog_finished_at") is not None


def test_catalog_stores_msgid_and_uidvalidity(tmp_path: Path) -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_message("1", sender_email="a@x.example", msgid="777")
    fake.mailbox("INBOX").uidvalidity = 42
    database = make_database(tmp_path)

    catalog_messages(fake, database)

    assert [message.msgid for message in list_messages(database)] == ["777"]
    assert get_meta(database, "uidvalidity") == "42"


def test_uidvalidity_change_aborts_catalog(tmp_path: Path) -> None:
    fake = make_inbox(2)
    database = make_database(tmp_path)
    catalog_messages(fake, database)
    fake.mailbox("INBOX").uidvalidity = 2

    with pytest.raises(ImapError):
        catalog_messages(fake, database)


def test_old_database_is_migrated_with_msgid_column(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE messages (uid TEXT PRIMARY KEY, sender TEXT NOT NULL, "
        "sender_name TEXT NOT NULL, domain TEXT NOT NULL, subject TEXT NOT NULL, "
        "internal_date TEXT NOT NULL, size INTEGER NOT NULL, seen INTEGER NOT NULL, "
        "has_attachment INTEGER NOT NULL, labels TEXT NOT NULL, fetched_at TEXT NOT NULL)"
    )
    raw.execute("INSERT INTO messages VALUES ('1','a','','x','s','d',1,0,0,'[]','t')")
    raw.commit()
    raw.close()

    database = connect(path)

    assert [message.msgid for message in list_messages(database)] == [""]
