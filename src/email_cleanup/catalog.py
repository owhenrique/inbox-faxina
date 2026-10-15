from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime

from .config import FETCH_BATCH
from .db import Message, get_meta, insert_messages, known_uids, now_iso, set_meta
from .imap import (
    INBOX,
    ImapConnection,
    ImapError,
    batches,
    fetch_capabilities,
    read_uidvalidity,
    server_detail,
)

GMAIL_LABELS_CAPABILITY = "X-GM-EXT-1"
FETCH_FIELDS = (
    "UID",
    "RFC822.SIZE",
    "INTERNALDATE",
    "FLAGS",
    "ENVELOPE",
    "BODYSTRUCTURE",
)
GMAIL_FETCH_FIELDS = ("X-GM-LABELS", "X-GM-MSGID")
FIELD_KEYS = frozenset({*FETCH_FIELDS, *GMAIL_FETCH_FIELDS})


def catalog_messages(
    connection: ImapConnection,
    database: sqlite3.Connection,
    *,
    refresh: bool = False,
    limit: int | None = None,
    batch_size: int = FETCH_BATCH,
    on_progress: Callable[[int, int], None] | None = None,
    account: str | None = None,
) -> int:
    status, data = connection.examine(INBOX)
    if status != "OK":
        raise ImapError(f"Não foi possível abrir a INBOX: {server_detail(data)}")
    status, data = connection.uid("SEARCH", "ALL")
    if status != "OK":
        raise ImapError(f"Falha ao buscar mensagens na INBOX: {server_detail(data)}")
    _check_uidvalidity(connection, database)
    known = set() if refresh else known_uids(database)
    pending = [uid for uid in _parse_search_uids(data) if uid not in known]
    complete = limit is None or len(pending) <= limit
    if limit is not None:
        pending = pending[:limit]
    total = len(pending)
    items = _fetch_items(connection)
    done = 0
    cataloged = 0
    for batch in batches(pending, batch_size):
        status, data = connection.uid("FETCH", ",".join(batch), items)
        if status != "OK":
            raise ImapError(
                f"Falha ao baixar um lote de mensagens: {server_detail(data)}"
            )
        fetched_at = now_iso()
        messages = [
            message
            for message in (
                parse_fetch_line(line, fetched_at) for line in data if line is not None
            )
            if message is not None
        ]
        insert_messages(database, messages)
        set_meta(database, "last_uid", batch[-1])
        cataloged += len(messages)
        done += len(batch)
        if on_progress is not None:
            on_progress(done, total)
    if account is not None:
        set_meta(database, "account", account)
    if complete:
        set_meta(database, "catalog_finished_at", now_iso())
    return cataloged


def parse_fetch_line(line: bytes, fetched_at: str) -> Message | None:
    text = line.decode("utf-8", errors="replace")
    fields = next(
        (token for token in parse_tokens(text) if isinstance(token, list)), None
    )
    if fields is None:
        return None
    values = _field_values(fields)
    uid = values.get("UID")
    if not isinstance(uid, str):
        return None
    subject, sender_name, sender = _decode_envelope(values.get("ENVELOPE"))
    return Message(
        uid=uid,
        sender=sender,
        sender_name=sender_name,
        domain=sender.partition("@")[2].lower(),
        subject=subject,
        internal_date=_parse_internal_date(values.get("INTERNALDATE")),
        size=_parse_int(values.get("RFC822.SIZE")),
        seen=_has_seen_flag(values.get("FLAGS")),
        has_attachment=_contains_attachment(values.get("BODYSTRUCTURE")),
        labels=_extract_labels(values.get("X-GM-LABELS")),
        fetched_at=fetched_at,
        msgid=_parse_msgid(values.get("X-GM-MSGID")),
    )


def parse_tokens(text: str) -> list[object]:
    tokens: list[object] = []
    stack: list[list[object]] = [tokens]
    index = 0
    while index < len(text):
        char = text[index]
        if char == "(":
            nested: list[object] = []
            stack[-1].append(nested)
            stack.append(nested)
            index += 1
        elif char == ")":
            if len(stack) > 1:
                stack.pop()
            index += 1
        elif char == '"':
            index += 1
            chars: list[str] = []
            while index < len(text) and text[index] != '"':
                if text[index] == "\\" and index + 1 < len(text):
                    chars.append(text[index + 1])
                    index += 2
                else:
                    chars.append(text[index])
                    index += 1
            index += 1
            stack[-1].append("".join(chars))
        elif char.isspace():
            index += 1
        else:
            start = index
            while index < len(text) and not text[index].isspace() and text[index] not in "()":
                index += 1
            atom = text[start:index]
            stack[-1].append(None if atom.upper() == "NIL" else atom)
    return tokens


def _field_values(fields: list[object]) -> dict[str, object]:
    values: dict[str, object] = {}
    index = 0
    while index + 1 < len(fields):
        key = fields[index]
        if isinstance(key, str) and key.upper() in FIELD_KEYS:
            values[key.upper()] = fields[index + 1]
            index += 2
        else:
            index += 1
    return values


def _decode_envelope(envelope: object) -> tuple[str, str, str]:
    if not isinstance(envelope, list):
        return "", "", ""
    subject = envelope[1] if len(envelope) > 1 else ""
    sender_name, sender = _decode_address(envelope[2] if len(envelope) > 2 else None)
    return _decode_subject(subject if isinstance(subject, str) else ""), sender_name, sender


def _decode_address(addresses: object) -> tuple[str, str]:
    if not isinstance(addresses, list) or not addresses:
        return "", ""
    first = addresses[0]
    if not isinstance(first, list) or len(first) < 4:
        return "", ""
    name = first[0] if isinstance(first[0], str) else ""
    mailbox = first[2] if isinstance(first[2], str) else ""
    host = first[3] if isinstance(first[3], str) else ""
    if mailbox and host:
        sender = f"{mailbox}@{host}"
    else:
        sender = mailbox or host
    return name, sender.strip().lower()


def _decode_subject(raw: str) -> str:
    if not raw:
        return ""
    decoded: list[str] = []
    for value, charset in decode_header(raw):
        if not isinstance(value, bytes):
            decoded.append(value)
            continue
        try:
            decoded.append(value.decode(charset or "utf-8", errors="replace"))
        except (LookupError, UnicodeError):
            decoded.append(value.decode("utf-8", errors="replace"))
    return "".join(decoded)


def _parse_internal_date(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ""
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _parse_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _has_seen_flag(flags: object) -> bool:
    if not isinstance(flags, list):
        return False
    return any(
        isinstance(flag, str) and flag.casefold() == "\\seen" for flag in flags
    )


def _contains_attachment(structure: object) -> bool:
    if isinstance(structure, list):
        return any(_contains_attachment(item) for item in structure)
    return isinstance(structure, str) and structure.upper() == "ATTACHMENT"


def _extract_labels(labels: object) -> tuple[str, ...]:
    if not isinstance(labels, list):
        return ()
    return tuple(item for item in labels if isinstance(item, str))


def _parse_search_uids(data: Sequence[bytes | None]) -> list[str]:
    uids: set[int] = set()
    for item in data:
        if not item:
            continue
        for token in item.decode("ascii", errors="replace").split():
            try:
                uids.add(int(token))
            except ValueError:
                continue
    return [str(uid) for uid in sorted(uids)]


def _parse_msgid(value: object) -> str:
    return value if isinstance(value, str) and value.isdigit() else ""


def _check_uidvalidity(connection: ImapConnection, database: sqlite3.Connection) -> None:
    current = read_uidvalidity(connection)
    if current is None:
        return
    stored = get_meta(database, "uidvalidity")
    if stored is not None and stored != current:
        raise ImapError(
            "O UIDVALIDITY da INBOX mudou: os UIDs do catálogo não valem mais. "
            "Apague .data/catalog.sqlite e catalogue de novo."
        )
    set_meta(database, "uidvalidity", current)


def _fetch_items(connection: ImapConnection) -> str:
    items = list(FETCH_FIELDS)
    if _gmail_labels_available(connection):
        items.extend(GMAIL_FETCH_FIELDS)
    return "(" + " ".join(items) + ")"


def _gmail_labels_available(connection: ImapConnection) -> bool:
    try:
        available = fetch_capabilities(connection)
    except ImapError:
        return False
    return GMAIL_LABELS_CAPABILITY in available
