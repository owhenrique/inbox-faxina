from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_CAPABILITIES: frozenset[str] = frozenset(
    {"IMAP4REV1", "UIDPLUS", "X-GM-EXT-1", "MOVE"}
)

PLAIN_BODYSTRUCTURE = (
    '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 1200 5)'
)

ATTACHMENT_BODYSTRUCTURE = (
    '(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 64 2)'
    '("APPLICATION" "PDF" ("NAME" "report.pdf") NIL NIL "BASE64" 2048 NIL'
    ' ("ATTACHMENT" ("FILENAME" "report.pdf"))) "MIXED")'
)

SUPPORTED_FETCH_ITEMS = frozenset(
    {
        "UID",
        "RFC822.SIZE",
        "INTERNALDATE",
        "FLAGS",
        "ENVELOPE",
        "BODYSTRUCTURE",
        "X-GM-LABELS",
    }
)

GMAIL_ITEMS = frozenset({"X-GM-LABELS"})

GMAIL_CAPABILITY = "X-GM-EXT-1"


def _quoted(value: str | None) -> str:
    if value is None:
        return "NIL"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_values(values: Sequence[str]) -> str:
    return " ".join(value if value.startswith("\\") else _quoted(value) for value in values)


def _parse_list(text: str) -> list[str]:
    body = text.strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1]
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(body):
        char = body[index]
        if quoted:
            if char == "\\" and index + 1 < len(body):
                current.append(body[index + 1])
                index += 2
                continue
            if char == '"':
                quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
        index += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _parse_uid_set(spec: str, known: Sequence[int]) -> set[int]:
    ordered = sorted(known)
    if not ordered:
        return set()
    last = ordered[-1]
    result: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "*":
            result.add(last)
            continue
        if ":" in token:
            start_text, _, end_text = token.partition(":")
            start = last if start_text == "*" else int(start_text)
            end = last if end_text == "*" else int(end_text)
            low, high = sorted((start, end))
            result.update(range(low, high + 1))
            continue
        result.add(int(token))
    return result


@dataclass
class FakeMessage:
    uid: str
    size: int = 1024
    internaldate: str = "01-Jan-2024 09:00:00 +0000"
    flags: list[str] = field(default_factory=list)
    subject: str = ""
    sender_name: str = ""
    sender_email: str = ""
    labels: list[str] = field(default_factory=list)
    bodystructure: str = PLAIN_BODYSTRUCTURE

    def envelope(self) -> str:
        address = self._address()
        return (
            f"({_quoted(self.internaldate)} {_quoted(self.subject)}"
            f" ({address}) ({address}) ({address}) NIL NIL NIL NIL NIL)"
        )

    def _address(self) -> str:
        mailbox, _, host = self.sender_email.partition("@")
        return f"({_quoted(self.sender_name or None)} NIL {_quoted(mailbox)} {_quoted(host)})"

    def fetch_fragment(self, item: str) -> str:
        if item == "UID":
            return f"UID {self.uid}"
        if item == "RFC822.SIZE":
            return f"RFC822.SIZE {self.size}"
        if item == "INTERNALDATE":
            return f"INTERNALDATE {_quoted(self.internaldate)}"
        if item == "FLAGS":
            return f"FLAGS ({' '.join(self.flags)})"
        if item == "ENVELOPE":
            return f"ENVELOPE {self.envelope()}"
        if item == "BODYSTRUCTURE":
            return f"BODYSTRUCTURE {self.bodystructure}"
        return f"X-GM-LABELS ({_render_values(self.labels)})"


@dataclass
class Mailbox:
    name: str
    flags: list[str] = field(default_factory=list)
    messages: list[FakeMessage] = field(default_factory=list)


@dataclass(frozen=True)
class RecordedCommand:
    name: str
    args: tuple[str, ...] = ()

    @property
    def subcommand(self) -> str | None:
        if self.name == "uid" and self.args:
            return self.args[0].upper()
        return None


class FakeIMAP4_SSL:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.capabilities: set[str] = set(DEFAULT_CAPABILITIES)
        self.mailboxes: dict[str, Mailbox] = {}
        self.selected: str | None = None
        self.readonly = False
        self.logged_in = False
        self.logged_out = False
        self.login_args: tuple[str, str] | None = None
        self.reject_login = False
        self.history: list[RecordedCommand] = []
        self._failures: dict[str, list[bytes]] = {}

    def add_mailbox(
        self,
        name: str,
        flags: Sequence[str] = (),
        messages: Sequence[FakeMessage] = (),
    ) -> Mailbox:
        mailbox = Mailbox(name=name, flags=list(flags), messages=list(messages))
        self.mailboxes[name] = mailbox
        return mailbox

    def add_message(
        self,
        uid: str,
        mailbox: str = "INBOX",
        *,
        size: int = 1024,
        internaldate: str = "01-Jan-2024 09:00:00 +0000",
        flags: Sequence[str] = (),
        subject: str = "",
        sender_name: str = "",
        sender_email: str = "",
        labels: Sequence[str] = (),
        bodystructure: str = PLAIN_BODYSTRUCTURE,
    ) -> FakeMessage:
        message = FakeMessage(
            uid=uid,
            size=size,
            internaldate=internaldate,
            flags=list(flags),
            subject=subject,
            sender_name=sender_name,
            sender_email=sender_email,
            labels=list(labels),
            bodystructure=bodystructure,
        )
        container = self.mailboxes.get(mailbox)
        if container is None:
            container = self.add_mailbox(mailbox)
        container.messages.append(message)
        return message

    def mailbox(self, name: str) -> Mailbox:
        return self.mailboxes[name]

    def fail_next(self, command: str, text: bytes = b"simulated command failure") -> None:
        self._failures.setdefault(command.upper(), []).append(text)

    def commands(self, name: str | None = None) -> list[RecordedCommand]:
        if name is None:
            return list(self.history)
        wanted = name.lower()
        return [record for record in self.history if record.name == wanted]

    def uid_calls(self, subcommand: str | None = None) -> list[RecordedCommand]:
        calls = self.commands("uid")
        if subcommand is None:
            return calls
        wanted = subcommand.upper()
        return [record for record in calls if record.subcommand == wanted]

    def login(self, user: str, password: str) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name="login", args=(user, password)))
        failure = self._take_failure("LOGIN")
        if failure is not None:
            return "NO", [failure]
        if self.reject_login:
            return "NO", [b"[AUTHENTICATIONFAILED] Invalid credentials"]
        self.logged_in = True
        self.login_args = (user, password)
        return "OK", [b"LOGIN completed"]

    def logout(self) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name="logout"))
        self.logged_in = False
        self.logged_out = True
        return "BYE", [b"LOGOUT completed"]

    def capability(self) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name="capability"))
        failure = self._take_failure("CAPABILITY")
        if failure is not None:
            return "NO", [failure]
        return "OK", [" ".join(sorted(self.capabilities)).encode()]

    def list(self, directory: str, pattern: str) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name="list", args=(directory, pattern)))
        failure = self._take_failure("LIST")
        if failure is not None:
            return "NO", [failure]
        lines = []
        for mailbox in self.mailboxes.values():
            flags = " ".join(mailbox.flags)
            lines.append(f"({flags}) \"/\" {_quoted(mailbox.name)}".encode())
        return "OK", lines

    def select(self, mailbox: str) -> tuple[str, list[bytes | None]]:
        return self._open_mailbox("select", mailbox)

    def examine(self, mailbox: str) -> tuple[str, list[bytes | None]]:
        return self._open_mailbox("examine", mailbox)

    def uid(self, command: str, *args: str) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name="uid", args=(command, *args)))
        subcommand = command.upper()
        failure = self._take_failure(subcommand)
        if failure is not None:
            return "NO", [failure]
        if self.selected is None:
            return "NO", [b"[CLIENTBUG] No mailbox selected"]
        if self.readonly and subcommand in {"STORE", "MOVE", "EXPUNGE"}:
            return "NO", [b"[READ-ONLY] Mailbox is selected read-only"]
        if subcommand == "SEARCH":
            return self._search(args)
        if subcommand == "FETCH":
            return self._fetch(args)
        if subcommand == "STORE":
            return self._store(args)
        if subcommand == "MOVE":
            return self._move(args)
        if subcommand == "EXPUNGE":
            return self._expunge(args)
        raise ValueError(f"unsupported UID command: {command}")

    def _open_mailbox(self, method: str, mailbox: str) -> tuple[str, list[bytes | None]]:
        self.history.append(RecordedCommand(name=method, args=(mailbox,)))
        failure = self._take_failure(method.upper())
        if failure is not None:
            return "NO", [failure]
        container = self.mailboxes.get(mailbox)
        if container is None:
            return "NO", [b"[NONEXISTENT] Unknown mailbox"]
        self.selected = mailbox
        self.readonly = method == "examine"
        return "OK", [str(len(container.messages)).encode()]

    def _search(self, args: tuple[str, ...]) -> tuple[str, list[bytes | None]]:
        criteria = " ".join(arg for arg in args if arg is not None).strip()
        if criteria.upper() != "ALL":
            raise ValueError(f"unsupported SEARCH criteria: {criteria}")
        container = self._selected_mailbox()
        payload = " ".join(message.uid for message in container.messages).encode()
        return "OK", [payload]

    def _fetch(self, args: tuple[str, ...]) -> tuple[str, list[bytes | None]]:
        if len(args) != 2:
            raise ValueError("FETCH expects a uid set and an item list")
        uid_set, raw_items = args
        items = raw_items.strip().strip("()").upper().split()
        for item in items:
            if item not in SUPPORTED_FETCH_ITEMS:
                raise ValueError(f"unsupported FETCH item: {item}")
        if GMAIL_ITEMS & set(items) and GMAIL_CAPABILITY not in self.capabilities:
            return "NO", [f"Unsupported item: {sorted(GMAIL_ITEMS & set(items))[0]}".encode()]
        container = self._selected_mailbox()
        wanted = _parse_uid_set(uid_set, [int(message.uid) for message in container.messages])
        lines = []
        for index, message in enumerate(container.messages):
            if int(message.uid) not in wanted:
                continue
            fragments = " ".join(message.fetch_fragment(item) for item in items)
            lines.append(f"{index + 1} ({fragments})".encode())
        return "OK", lines

    def _store(self, args: tuple[str, ...]) -> tuple[str, list[bytes | None]]:
        if len(args) != 3:
            raise ValueError("STORE expects a uid set, an action and a value list")
        uid_set, raw_action, raw_values = args
        action = raw_action.upper()
        silent = action.endswith(".SILENT")
        if silent:
            action = action[: -len(".SILENT")]
        values = _parse_list(raw_values)
        gmail = "X-GM-LABELS" in action
        if gmail and GMAIL_CAPABILITY not in self.capabilities:
            return "NO", [b"Unsupported item: X-GM-LABELS"]
        if gmail:
            attribute = "X-GM-LABELS"
        elif "FLAGS" in action:
            attribute = "FLAGS"
        else:
            raise ValueError(f"unsupported STORE action: {raw_action}")
        operation = action[0] if action[0] in "+-" else "="
        container = self._selected_mailbox()
        wanted = _parse_uid_set(uid_set, [int(message.uid) for message in container.messages])
        lines = []
        for index, message in enumerate(container.messages):
            if int(message.uid) not in wanted:
                continue
            current = message.labels if gmail else message.flags
            if operation == "+":
                current.extend(value for value in values if value not in current)
            elif operation == "-":
                removed = {value.lower() for value in values}
                current[:] = [value for value in current if value.lower() not in removed]
            else:
                current[:] = values
            if not silent:
                rendered = _render_values(current) if gmail else " ".join(current)
                lines.append(f"{index + 1} (UID {message.uid} {attribute} ({rendered}))".encode())
        if silent:
            return "OK", [None]
        return "OK", lines

    def _move(self, args: tuple[str, ...]) -> tuple[str, list[bytes | None]]:
        if len(args) != 2:
            raise ValueError("MOVE expects a uid set and a destination mailbox")
        uid_set, destination = args
        target = self.mailboxes.get(destination)
        if target is None:
            return "NO", [b"[TRYCREATE] Unknown destination mailbox"]
        container = self._selected_mailbox()
        wanted = _parse_uid_set(uid_set, [int(message.uid) for message in container.messages])
        moved = []
        for message in container.messages:
            if int(message.uid) in wanted:
                moved.append(message)
        container.messages[:] = [
            message for message in container.messages if int(message.uid) not in wanted
        ]
        target.messages.extend(moved)
        return "OK", [f"MOVED {len(moved)}".encode()]

    def _expunge(self, args: tuple[str, ...]) -> tuple[str, list[bytes | None]]:
        if len(args) > 1:
            raise ValueError("EXPUNGE accepts at most one uid set")
        container = self._selected_mailbox()
        wanted = (
            _parse_uid_set(args[0], [int(message.uid) for message in container.messages])
            if args
            else {int(message.uid) for message in container.messages}
        )
        removed_sequences = [
            str(index + 1)
            for index, message in enumerate(container.messages)
            if int(message.uid) in wanted and "\\Deleted" in message.flags
        ]
        container.messages[:] = [
            message
            for message in container.messages
            if not (int(message.uid) in wanted and "\\Deleted" in message.flags)
        ]
        if not removed_sequences:
            return "OK", [None]
        return "OK", [sequence.encode() for sequence in removed_sequences]

    def _selected_mailbox(self) -> Mailbox:
        container = self.mailboxes[self.selected or ""]
        return container

    def _take_failure(self, command: str) -> bytes | None:
        pending = self._failures.get(command.upper())
        if not pending:
            return None
        return pending.pop(0)
