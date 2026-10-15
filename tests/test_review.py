from __future__ import annotations

from email_cleanup.classify import build_groups
from email_cleanup.db import Message
from email_cleanup.review import danger_warning, group_description


def make_message(uid: str, *, sender: str, subject: str) -> Message:
    return Message(
        uid=uid,
        sender=sender,
        sender_name="Nome",
        domain=sender.split("@", 1)[1],
        subject=subject,
        internal_date="2024-01-01T10:00:00+00:00",
        size=1024,
        seen=False,
        has_attachment=False,
        labels=("\\Inbox",),
        fetched_at="2026-10-15T10:00:00+00:00",
    )


def test_group_description_includes_danger_when_sensitive() -> None:
    messages = [make_message(str(i), sender="maria@gmail.com", subject="Oi") for i in range(3)]
    group = build_groups(messages, min_count=1)[0]
    description = group_description(group)
    assert "⚠ 3 sensíveis" in description
    assert "pessoal" in description


def test_group_description_has_no_danger_when_clean() -> None:
    messages = [make_message("1", sender="loja@shop.example", subject="Oferta")]
    group = build_groups(messages, min_count=1)[0]
    assert "⚠" not in group_description(group)


def test_danger_warning_only_for_trash_and_delete() -> None:
    messages = [
        make_message("1", sender="maria@gmail.com", subject="Oi"),
        make_message("2", sender="loja@shop.example", subject="Oferta"),
    ]
    warning = danger_warning(messages, "lixeira")
    assert warning is not None
    assert "1 de 2" in warning
    assert "pessoal: 1" in warning
    assert danger_warning(messages, "apagar") is not None
    assert danger_warning(messages, "arquivar") is None
    assert danger_warning(messages, "manter") is None


def test_danger_warning_none_when_nothing_sensitive() -> None:
    messages = [make_message("1", sender="loja@shop.example", subject="Oferta")]
    assert danger_warning(messages, "apagar") is None
