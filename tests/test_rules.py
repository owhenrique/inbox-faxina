from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from email_cleanup.db import Message, connect, record_manifest
from email_cleanup.rules import (
    ACTIONS,
    RuleError,
    build_plan,
    matches,
    pending_by_action,
    rule_hit_counts,
    save_rule,
)

MB = 1024 * 1024
NOW = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)


def make_message(
    uid: str,
    *,
    sender: str = "news@shop.example",
    domain: str = "shop.example",
    subject: str = "Subject",
    internal_date: str = "2024-01-01T10:00:00+00:00",
    size: int = 1024,
) -> Message:
    return Message(
        uid=uid,
        sender=sender,
        sender_name="Name",
        domain=domain,
        subject=subject,
        internal_date=internal_date,
        size=size,
        seen=False,
        has_attachment=False,
        labels=("\\Inbox",),
        fetched_at="2026-10-15T10:00:00+00:00",
    )


@pytest.fixture
def connection() -> sqlite3.Connection:
    return connect(":memory:")  # type: ignore[arg-type]


def test_actions_are_the_four_decisions() -> None:
    assert ACTIONS == ("manter", "arquivar", "lixeira", "apagar")


def test_matches_sender_and_domain() -> None:
    message = make_message("1")
    assert matches({"sender": "news@shop.example"}, message, NOW)
    assert not matches({"sender": "other@shop.example"}, message, NOW)
    assert matches({"domain": "shop.example"}, message, NOW)
    assert not matches({"domain": "other.example"}, message, NOW)


def test_matches_category() -> None:
    promo = make_message("1", subject="Grande oferta")
    plain = make_message("2", sender="ana@pessoa.example", subject="Oi")
    assert matches({"category": "promocao"}, promo, NOW)
    assert not matches({"category": "promocao"}, plain, NOW)
    notification = make_message("3", sender="noreply@bank.example")
    assert matches({"category": "notificacao"}, notification, NOW)


def test_matches_age_and_size() -> None:
    old = make_message("1", internal_date="2019-01-01T00:00:00+00:00", size=30 * MB)
    recent = make_message("2", internal_date="2026-09-01T00:00:00+00:00", size=1024)
    assert matches({"older_than_years": 5}, old, NOW)
    assert not matches({"older_than_years": 5}, recent, NOW)
    assert matches({"min_size_mb": 20}, old, NOW)
    assert not matches({"min_size_mb": 20}, recent, NOW)


def test_matches_requires_every_key() -> None:
    old_large = make_message("1", internal_date="2019-01-01T00:00:00+00:00", size=30 * MB)
    assert matches({"older_than_years": 5, "min_size_mb": 20}, old_large, NOW)
    assert not matches({"older_than_years": 5, "domain": "other.example"}, old_large, NOW)


def test_save_rule_rejects_unknown_action(connection: sqlite3.Connection) -> None:
    with pytest.raises(RuleError):
        save_rule(connection, "explodir", {"domain": "a.example"}, "x")


def test_save_rule_rejects_empty_filter(connection: sqlite3.Connection) -> None:
    with pytest.raises(RuleError):
        save_rule(connection, "arquivar", {}, "x")


def test_last_matching_rule_wins(connection: sqlite3.Connection) -> None:
    save_rule(connection, "arquivar", {"domain": "shop.example"}, "domínio")
    second = save_rule(connection, "manter", {"sender": "news@shop.example"}, "remetente")
    plan = build_plan(connection, [make_message("1")], NOW)
    assert plan.actions["1"].action == "manter"
    assert plan.actions["1"].rule_id == second


def test_conflicts_report_uids_hit_by_several_rules(connection: sqlite3.Connection) -> None:
    first = save_rule(connection, "arquivar", {"domain": "shop.example"}, "domínio")
    second = save_rule(connection, "lixeira", {"sender": "news@shop.example"}, "remetente")
    messages = [make_message("1"), make_message("2", sender="x@shop.example")]
    plan = build_plan(connection, messages, NOW)
    assert plan.conflicts == {"1": (first, second)}
    assert plan.actions["2"].rule_id == first


def test_unmatched_uids_have_no_action(connection: sqlite3.Connection) -> None:
    save_rule(connection, "arquivar", {"domain": "other.example"}, "outro")
    plan = build_plan(connection, [make_message("1")], NOW)
    assert plan.actions == {}
    assert plan.conflicts == {}


def test_totals_count_messages_and_size_per_action(connection: sqlite3.Connection) -> None:
    save_rule(connection, "arquivar", {"domain": "shop.example"}, "domínio")
    messages = [make_message("1", size=10), make_message("2", size=30)]
    plan = build_plan(connection, messages, NOW)
    assert plan.totals["arquivar"].count == 2
    assert plan.totals["arquivar"].size == 40
    assert plan.totals["lixeira"].count == 0


def test_rule_hit_counts(connection: sqlite3.Connection) -> None:
    first = save_rule(connection, "arquivar", {"domain": "shop.example"}, "domínio")
    second = save_rule(connection, "lixeira", {"sender": "news@shop.example"}, "remetente")
    messages = [make_message("1"), make_message("2", sender="x@shop.example")]
    assert rule_hit_counts(connection, messages, NOW) == {first: 2, second: 1}


def test_pending_skips_manter_and_manifest_ok(connection: sqlite3.Connection) -> None:
    save_rule(connection, "arquivar", {"domain": "shop.example"}, "domínio")
    save_rule(connection, "manter", {"sender": "keep@shop.example"}, "manter")
    messages = [
        make_message("1"),
        make_message("2"),
        make_message("3", sender="keep@shop.example"),
    ]
    record_manifest(connection, "1", "arquivar", "batch", "ok")
    record_manifest(connection, "2", "arquivar", "batch", "error")
    plan = build_plan(connection, messages, NOW)
    pending = pending_by_action(connection, plan)
    assert pending == {"arquivar": ["2"]}
