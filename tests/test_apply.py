from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from rich.console import Console

from email_cleanup import apply as apply_module
from email_cleanup.apply import (
    CONFIRM_APPLY,
    CONFIRM_DELETE,
    confirmation_phrase,
    execute,
    run_apply,
    sender_sample,
)
from email_cleanup.db import Message, connect, insert_messages, succeeded_uids
from email_cleanup.imap import ImapClient
from email_cleanup.rules import save_rule

from fake_imap import FakeIMAP4_SSL

TRASH = "[Gmail]/Lixeira"
NOW = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)


def make_message(uid: str, sender: str = "news@shop.example") -> Message:
    return Message(
        uid=uid,
        sender=sender,
        sender_name="Name",
        domain=sender.split("@")[1],
        subject="Subject",
        internal_date="2024-01-01T10:00:00+00:00",
        size=1024,
        seen=False,
        has_attachment=False,
        labels=("\\Inbox",),
        fetched_at="2026-10-15T10:00:00+00:00",
    )


def build_fake(count: int) -> FakeIMAP4_SSL:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    fake.add_mailbox(TRASH, flags=["\\HasNoChildren", "\\Trash"])
    for uid in range(1, count + 1):
        fake.add_message(str(uid), sender_email="news@shop.example", labels=["\\Inbox"])
    return fake


def build_client(fake: FakeIMAP4_SSL) -> ImapClient:
    client = ImapClient("ana@gmail.com", "pw", imap_factory=lambda host, port: fake)
    client.connect()
    return client


@pytest.fixture
def database() -> sqlite3.Connection:
    connection = connect(":memory:")  # type: ignore[arg-type]
    insert_messages(connection, [make_message(str(uid)) for uid in range(1, 6)])
    return connection


class Session:
    def __init__(self, fake: FakeIMAP4_SSL) -> None:
        self.fake = fake
        self.opened = 0

    def factory(self) -> ImapClient:
        self.opened += 1
        return build_client(self.fake)


def answering(*answers: str):
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def raising(error: BaseException):
    def prompt(_: str) -> str:
        raise error

    return prompt


def silent() -> Console:
    return Console(quiet=True)


def test_confirmation_phrase_depends_on_delete() -> None:
    assert confirmation_phrase({"arquivar": ["1"]}) == CONFIRM_APPLY == "APLICAR"
    assert confirmation_phrase({"arquivar": ["1"], "apagar": ["2"]}) == CONFIRM_DELETE
    assert CONFIRM_DELETE == "APAGAR DEFINITIVO"


def test_sender_sample_limits_and_dedupes() -> None:
    messages = [make_message(str(i), sender=f"s{i}@x.example") for i in range(15)]
    messages.append(make_message("99", sender="s0@x.example"))
    sample = sender_sample(messages, [m.uid for m in messages], limit=10)
    assert len(sample) == 10
    assert len(set(sample)) == 10


def test_execute_archive_records_manifest(database: sqlite3.Connection) -> None:
    fake = build_fake(5)
    result = execute(build_client(fake), database, {"arquivar": ["1", "2"]})
    stores = fake.uid_calls("STORE")
    assert [call.args[2:] for call in stores] == [("-X-GM-LABELS", "(\\Inbox)")]
    assert succeeded_uids(database, "arquivar") == {"1", "2"}
    assert result.done == {"arquivar": 2}
    assert result.error is None


def test_execute_trash_moves_to_discovered_folder(database: sqlite3.Connection) -> None:
    fake = build_fake(5)
    execute(build_client(fake), database, {"lixeira": ["1", "2", "3"]})
    assert [call.args[2] for call in fake.uid_calls("MOVE")] == [TRASH]
    assert len(fake.mailbox(TRASH).messages) == 3
    assert succeeded_uids(database, "lixeira") == {"1", "2", "3"}


def test_execute_delete_purges_from_trash(database: sqlite3.Connection) -> None:
    fake = build_fake(5)
    execute(build_client(fake), database, {"apagar": ["1", "2"]})
    assert fake.mailbox(TRASH).messages == []
    assert len(fake.mailbox("INBOX").messages) == 3
    assert succeeded_uids(database, "apagar") == {"1", "2"}


def test_execute_batches_by_action_batch(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply_module, "ACTION_BATCH", 2)
    fake = build_fake(5)
    execute(build_client(fake), database, {"arquivar": ["1", "2", "3", "4", "5"]})
    assert len(fake.uid_calls("STORE")) == 3
    batch_ids = {
        row[0] for row in database.execute("SELECT DISTINCT batch_id FROM manifest")
    }
    assert len(batch_ids) == 3


def test_execute_failure_records_batch_and_stops(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply_module, "ACTION_BATCH", 2)
    fake = build_fake(5)
    fake.fail_next("STORE")
    result = execute(build_client(fake), database, {"arquivar": ["1", "2", "3", "4"]})
    assert result.error is not None
    assert result.done == {}
    assert succeeded_uids(database, "arquivar") == set()
    statuses = {row[0] for row in database.execute("SELECT status FROM manifest")}
    assert statuses == {"erro"}
    assert len(fake.uid_calls("STORE")) == 1


def test_dry_run_sends_nothing(database: sqlite3.Connection) -> None:
    save_rule(database, "arquivar", {"domain": "shop.example"}, "domínio")
    session = Session(build_fake(5))
    code = run_apply(
        database, silent(), session.factory, dry_run=True, confirm=answering(), now=NOW
    )
    assert code == 0
    assert session.opened == 0


def test_apply_without_rules_guides_to_review(database: sqlite3.Connection) -> None:
    session = Session(build_fake(5))
    code = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=answering(), now=NOW
    )
    assert code == 1
    assert session.opened == 0


def test_wrong_confirmation_executes_nothing(database: sqlite3.Connection) -> None:
    save_rule(database, "arquivar", {"domain": "shop.example"}, "domínio")
    session = Session(build_fake(5))
    code = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=answering("sim"), now=NOW
    )
    assert code == 1
    assert session.opened == 0
    assert succeeded_uids(database, "arquivar") == set()


@pytest.mark.parametrize("error", [KeyboardInterrupt(), EOFError()])
def test_interrupted_prompt_executes_nothing(
    database: sqlite3.Connection, error: BaseException
) -> None:
    save_rule(database, "arquivar", {"domain": "shop.example"}, "domínio")
    session = Session(build_fake(5))
    code = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=raising(error), now=NOW
    )
    assert code != 0
    assert session.opened == 0


def test_confirmed_apply_executes_and_skips_done_on_rerun(
    database: sqlite3.Connection,
) -> None:
    save_rule(database, "arquivar", {"domain": "shop.example"}, "domínio")
    fake = build_fake(5)
    session = Session(fake)
    code = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=answering("APLICAR"), now=NOW
    )
    assert code == 0
    assert succeeded_uids(database, "arquivar") == {"1", "2", "3", "4", "5"}
    second = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=answering("APLICAR"), now=NOW
    )
    assert second == 0
    assert len(fake.uid_calls("STORE")) == 1


def test_delete_plan_requires_destructive_phrase(database: sqlite3.Connection) -> None:
    save_rule(database, "apagar", {"domain": "shop.example"}, "apagar")
    session = Session(build_fake(5))
    code = run_apply(
        database, silent(), session.factory, dry_run=False, confirm=answering("APLICAR"), now=NOW
    )
    assert code == 1
    assert session.opened == 0
    code = run_apply(
        database,
        silent(),
        session.factory,
        dry_run=False,
        confirm=answering("APAGAR DEFINITIVO"),
        now=NOW,
    )
    assert code == 0
    assert succeeded_uids(database, "apagar") == {"1", "2", "3", "4", "5"}
