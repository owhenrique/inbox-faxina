from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from email_cleanup import cli
from email_cleanup.config import ConfigError
from email_cleanup.db import Message, connect, insert_messages
from email_cleanup.rules import save_rule


def test_require_terminal_rejects_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    with pytest.raises(ConfigError) as error:
        cli.require_terminal()

    assert "terminal interativo" in str(error.value)


def test_apply_without_tty_exits_nonzero_before_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog.sqlite"
    database = connect(path)
    insert_messages(
        database,
        [
            Message(
                uid="1",
                sender="a@shop.example",
                sender_name="",
                domain="shop.example",
                subject="s",
                internal_date="2024-01-01T10:00:00+00:00",
                size=1,
                seen=False,
                has_attachment=False,
                labels=(),
                fetched_at="2026-10-15T10:00:00+00:00",
            )
        ],
    )
    save_rule(database, "arquivar", {"domain": "shop.example"}, "domínio")
    database.close()
    monkeypatch.setattr(cli, "database_path", lambda: path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(
        "builtins.input", lambda *_: pytest.fail("prompt must not be shown")
    )

    assert cli.main(["apply"]) == 1
