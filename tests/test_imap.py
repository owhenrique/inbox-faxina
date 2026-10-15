from __future__ import annotations

import pytest

from email_cleanup.imap import ImapClient, ImapError

from fake_imap import FakeIMAP4_SSL, RecordedCommand

USER = "ana@gmail.com"
PASSWORD = "S3cr3t-AppPassw0rd"
SPACED_PASSWORD = "S3cr3t App Passw0rd"
TRASH = "[Gmail]/Lixeira"


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


def build_client(fake: FakeIMAP4_SSL, password: str = PASSWORD) -> ImapClient:
    return ImapClient(USER, password, imap_factory=lambda host, port: fake)


def build_inbox(count: int, *, labels: tuple[str, ...] = ("\\Inbox", "Promo")) -> FakeIMAP4_SSL:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    for uid in range(1, count + 1):
        fake.add_message(str(uid), sender_email="news@shop.example", labels=list(labels))
    return fake


def build_with_trash(count: int = 0) -> FakeIMAP4_SSL:
    fake = build_inbox(count)
    fake.add_mailbox(TRASH, flags=["\\HasNoChildren", "\\Trash"])
    return fake


def fetch_items(call: RecordedCommand) -> set[str]:
    return set(call.args[2].strip("()").upper().split())


def search_terms(call: RecordedCommand) -> list[str]:
    return [arg for arg in call.args[1:] if arg is not None]


class RacingFakeIMAP4_SSL(FakeIMAP4_SSL):
    def uid(self, command: str, *args: str) -> tuple[str, list[bytes | None]]:
        status, data = super().uid(command, *args)
        if command.upper() == "MOVE" and status == "OK":
            self.mailboxes[args[1]].messages.clear()
        return status, data


def test_connect_uses_gmail_endpoint_and_app_password_without_spaces() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    created: list[tuple[str, int]] = []

    def factory(host: str, port: int) -> FakeIMAP4_SSL:
        created.append((host, port))
        return fake

    client = ImapClient(USER, SPACED_PASSWORD, imap_factory=factory)

    client.connect()

    assert created == [("imap.gmail.com", 993)]
    assert fake.login_args == (USER, "S3cr3tAppPassw0rd")
    assert client.connection is fake

    client.close()

    assert fake.logged_out is True


def test_context_manager_connects_and_logs_out() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)

    with build_client(fake) as client:
        assert fake.logged_in is True
        assert client.connection is fake

    assert fake.logged_out is True


@pytest.mark.parametrize("missing", ["UIDPLUS", "X-GM-EXT-1"])
def test_connect_requires_capabilities_without_leaking_password(missing: str) -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.capabilities.discard(missing)
    client = build_client(fake)

    with pytest.raises(ImapError) as error:
        client.connect()

    message = str(error.value)
    assert missing in message
    assert PASSWORD not in message


def test_connect_reports_rejected_login_without_password() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.reject_login = True
    client = build_client(fake)

    with pytest.raises(ImapError) as error:
        client.connect()

    assert fake.logged_in is False
    assert PASSWORD not in str(error.value)


def test_examine_inbox_is_read_only() -> None:
    fake = build_inbox(1)
    client = build_client(fake)
    client.connect()

    client.examine_inbox()

    assert fake.commands("examine")[0].args == ("INBOX",)
    assert fake.selected == "INBOX"
    assert fake.readonly is True


def test_find_trash_folder_uses_trash_flag_not_name() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX", flags=["\\HasNoChildren"])
    fake.add_mailbox("Arquivo", flags=["\\Archive"])
    fake.add_mailbox("Lixeira local", flags=["\\HasNoChildren"])
    fake.add_mailbox(TRASH, flags=["\\HasNoChildren", "\\Trash"])
    client = build_client(fake)
    client.connect()

    assert client.find_trash_folder() == TRASH
    assert fake.commands("list")


def test_find_trash_folder_never_guesses_localized_name() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    fake.add_mailbox(TRASH)
    fake.add_mailbox("Trash")
    client = build_client(fake)
    client.connect()

    with pytest.raises(ImapError) as error:
        client.find_trash_folder()

    assert "Trash" in str(error.value)


def test_find_trash_folder_propagates_list_failure() -> None:
    fake = build_with_trash()
    fake.fail_next("LIST", b"LIST is unavailable")
    client = build_client(fake)
    client.connect()

    with pytest.raises(ImapError) as error:
        client.find_trash_folder()

    assert "LIST is unavailable" in str(error.value)


def test_archive_removes_inbox_label() -> None:
    fake = build_inbox(2)
    client = build_client(fake)
    client.connect()

    client.archive(["1", "2"])

    calls = fake.uid_calls("STORE")
    assert len(calls) == 1
    command, uid_set, action, values = calls[0].args
    assert parse_uid_set(uid_set) == {"1", "2"}
    assert action.upper() == "-X-GM-LABELS"
    assert values.replace(" ", "") == "(\\Inbox)"
    assert fake.selected == "INBOX"
    assert [message.labels for message in fake.mailbox("INBOX").messages] == [["Promo"], ["Promo"]]


def test_archive_splits_uids_into_action_batches() -> None:
    fake = build_inbox(1)
    client = build_client(fake)
    client.connect()
    uids = [str(uid) for uid in range(1, 451)]

    client.archive(uids)

    calls = fake.uid_calls("STORE")
    assert len(calls) == 3
    assert sorted(len(parse_uid_set(call.args[1])) for call in calls) == [50, 200, 200]
    merged: set[str] = set()
    for call in calls:
        merged |= parse_uid_set(call.args[1])
        assert call.args[2].upper() == "-X-GM-LABELS"
        assert call.args[3].replace(" ", "") == "(\\Inbox)"
    assert merged == set(uids)


def test_move_to_trash_uses_discovered_folder() -> None:
    fake = build_with_trash(2)
    client = build_client(fake)
    client.connect()

    client.move_to_trash(["1", "2"])

    calls = fake.uid_calls("MOVE")
    assert [call.args[2] for call in calls] == [TRASH]
    assert parse_uid_set(calls[0].args[1]) == {"1", "2"}
    assert fake.mailbox("INBOX").messages == []
    assert len(fake.mailbox(TRASH).messages) == 2


def test_move_to_trash_splits_uids_into_action_batches() -> None:
    fake = build_with_trash()
    client = build_client(fake)
    client.connect()
    uids = [str(uid) for uid in range(1, 451)]

    client.move_to_trash(uids)

    calls = fake.uid_calls("MOVE")
    assert len(calls) == 3
    assert sorted(len(parse_uid_set(call.args[1])) for call in calls) == [50, 200, 200]
    assert {call.args[2] for call in calls} == {TRASH}


def test_delete_permanently_resolves_trash_uids_via_msgid() -> None:
    fake = FakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    fake.add_message("10", sender_email="news@shop.example", msgid="msg-a")
    fake.add_message("11", sender_email="news@shop.example", msgid="msg-b")
    fake.add_mailbox(TRASH, flags=["\\HasNoChildren", "\\Trash"])
    fake.add_message("3", mailbox=TRASH, sender_email="keep@example.com", msgid="msg-keep")
    client = build_client(fake)
    client.connect()

    purged = client.delete_permanently(["msg-a", "msg-b"])

    assert purged == {"msg-a", "msg-b"}
    assert fake.uid_calls("FETCH") == []
    moves = fake.uid_calls("MOVE")
    assert len(moves) == 1
    assert parse_uid_set(moves[0].args[1]) == {"10", "11"}
    assert moves[0].args[2] == TRASH
    for call in fake.uid_calls("SEARCH"):
        assert search_terms(call)[0].upper() == "X-GM-MSGID"
    stores = fake.uid_calls("STORE")
    assert len(stores) == 1
    assert parse_uid_set(stores[0].args[1]) == {"4", "5"}
    assert stores[0].args[2].upper().startswith("+FLAGS")
    assert stores[0].args[3].replace(" ", "") == "(\\Deleted)"
    expunges = fake.uid_calls("EXPUNGE")
    assert len(expunges) == 1
    assert parse_uid_set(expunges[0].args[1]) == {"4", "5"}
    assert fake.selected == TRASH
    assert [message.msgid for message in fake.mailbox(TRASH).messages] == ["msg-keep"]
    assert fake.mailbox("INBOX").messages == []


def test_delete_permanently_is_idempotent_for_messages_already_in_trash() -> None:
    fake = build_with_trash()
    fake.add_message("7", mailbox=TRASH, sender_email="a@x.example", msgid="msg-a")
    client = build_client(fake)
    client.connect()

    purged = client.delete_permanently(["msg-a", "msg-gone"])

    assert purged == {"msg-a"}
    assert fake.uid_calls("MOVE") == []
    assert fake.mailbox(TRASH).messages == []


def test_delete_permanently_splits_every_step_into_action_batches() -> None:
    fake = build_with_trash()
    uids = [str(uid) for uid in range(1, 451)]
    for uid in uids:
        fake.add_message(uid, sender_email="news@shop.example", msgid=f"msg-{uid}")
    client = build_client(fake)
    client.connect()

    client.delete_permanently([f"msg-{uid}" for uid in uids])

    for name in ("MOVE", "STORE", "EXPUNGE"):
        calls = fake.uid_calls(name)
        assert len(calls) == 3
        assert sorted(len(parse_uid_set(call.args[1])) for call in calls) == [50, 200, 200]


def test_delete_permanently_skips_messages_missing_from_trash() -> None:
    fake = RacingFakeIMAP4_SSL("imap.gmail.com", 993)
    fake.add_mailbox("INBOX")
    fake.add_message("1", sender_email="news@shop.example", msgid="msg-a")
    fake.add_mailbox(TRASH, flags=["\\Trash"])
    client = build_client(fake)
    client.connect()

    purged = client.delete_permanently(["msg-a"])

    assert purged == set()
    assert fake.uid_calls("MOVE")
    assert fake.uid_calls("STORE") == []
    assert fake.uid_calls("EXPUNGE") == []


def test_locate_in_inbox_maps_present_uids_to_msgids() -> None:
    fake = build_inbox(2)
    client = build_client(fake)
    client.connect()

    located = client.locate_in_inbox(["1", "2", "99"])

    assert set(located) == {"1", "2"}
    assert all(located.values())


def test_command_failure_propagates_as_imap_error() -> None:
    fake = build_inbox(1)
    fake.fail_next("STORE", b"NO [SERVERBUG] STORE rejected")
    client = build_client(fake)
    client.connect()

    with pytest.raises(ImapError) as error:
        client.archive(["1"])

    assert "STORE rejected" in str(error.value)
    assert PASSWORD not in str(error.value)


def test_default_factory_connection_supports_examine_and_quotes_names() -> None:
    from email_cleanup.imap import GmailIMAP4_SSL, quote_mailbox

    assert hasattr(GmailIMAP4_SSL, "examine")
    assert quote_mailbox("[Gmail]/Lixeira") == '"[Gmail]/Lixeira"'
    assert quote_mailbox('"já"') == '"já"'
