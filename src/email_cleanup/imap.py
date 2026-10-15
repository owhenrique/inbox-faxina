from __future__ import annotations

import imaplib
import re
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Protocol

from .config import ACTION_BATCH

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
INBOX = "INBOX"
REQUIRED_CAPABILITIES = ("UIDPLUS", "X-GM-EXT-1")
TRASH_FLAG = "\\Trash"

LIST_LINE_PATTERN = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?:NIL|"[^"]*")\s+(?P<name>.+)$'
)
MSGID_PATTERN = re.compile(rb"X-GM-MSGID\s+([^\s)]+)")


class ImapError(RuntimeError):
    pass


class ImapConnection(Protocol):
    def login(self, user: str, password: str) -> tuple[str, list[bytes | None]]: ...

    def logout(self) -> tuple[str, list[bytes | None]]: ...

    def capability(self) -> tuple[str, list[bytes | None]]: ...

    def list(self, directory: str, pattern: str) -> tuple[str, list[bytes | None]]: ...

    def select(self, mailbox: str) -> tuple[str, list[bytes | None]]: ...

    def examine(self, mailbox: str) -> tuple[str, list[bytes | None]]: ...

    def uid(self, command: str, *args: str) -> tuple[str, list[bytes | None]]: ...

    def response(self, code: str) -> tuple[str, list[bytes | None]]: ...


def default_imap_factory(host: str, port: int) -> imaplib.IMAP4_SSL:
    return imaplib.IMAP4_SSL(host, port)


def server_detail(data: Sequence[bytes | None]) -> str:
    return " ".join(
        item.decode("utf-8", errors="replace") for item in data if item
    ).strip()


def batches(uids: Sequence[str], size: int) -> list[list[str]]:
    return [list(uids[index : index + size]) for index in range(0, len(uids), size)]


def read_uidvalidity(connection: ImapConnection) -> str | None:
    try:
        _, data = connection.response("UIDVALIDITY")
    except (imaplib.IMAP4.error, KeyError, AttributeError):
        return None
    for item in reversed(list(data)):
        if item:
            value = item.decode("ascii", errors="replace").strip()
            if value.isdigit():
                return value
    return None


def fetch_capabilities(connection: ImapConnection) -> set[str]:
    status, data = connection.capability()
    if status != "OK":
        raise ImapError(
            f"Falha ao consultar as capacidades do servidor: {server_detail(data)}"
        )
    return {
        token.upper()
        for item in data
        if item
        for token in item.decode("ascii", errors="replace").split()
    }


class ImapClient:
    def __init__(
        self,
        address: str,
        app_password: str,
        *,
        host: str = GMAIL_HOST,
        port: int = GMAIL_PORT,
        imap_factory: Callable[[str, int], ImapConnection] = default_imap_factory,
    ) -> None:
        self.address = address
        self.host = host
        self.port = port
        self.connection: ImapConnection | None = None
        self._password = "".join(app_password.split())
        self._imap_factory = imap_factory
        self._selected: str | None = None
        self._readonly = False

    def connect(self) -> ImapConnection:
        connection = self._open_connection()
        self.connection = connection
        try:
            self._authenticate(connection)
            self._require_capabilities(connection)
        except ImapError:
            self.close()
            raise
        return connection

    def close(self) -> None:
        connection, self.connection = self.connection, None
        self._selected = None
        self._readonly = False
        if connection is None:
            return
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def __enter__(self) -> ImapClient:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def examine_inbox(self) -> None:
        self._select(INBOX, readonly=True)

    def find_trash_folder(self) -> str:
        connection = self._require_connection()
        status, data = connection.list('""', "*")
        if status != "OK":
            raise ImapError(f"Falha ao listar as pastas: {server_detail(data)}")
        for line in data:
            match = None if line is None else LIST_LINE_PATTERN.match(line)
            if match is None:
                continue
            flags = {
                flag.casefold()
                for flag in match.group("flags").decode("utf-8", errors="replace").split()
            }
            if TRASH_FLAG.casefold() not in flags:
                continue
            name = match.group("name").strip()
            if name.startswith(b'"') and name.endswith(b'"'):
                name = name[1:-1]
            return name.replace(b'\\"', b'"').replace(b"\\\\", b"\\").decode(
                "utf-8", errors="replace"
            )
        raise ImapError(
            "Pasta da Lixeira não encontrada: nenhuma pasta anunciou o sinalizador \\Trash."
        )

    def archive(self, uids: Sequence[str]) -> None:
        self._select(INBOX, readonly=False)
        for batch in batches(uids, ACTION_BATCH):
            self._uid("STORE", ",".join(batch), "-X-GM-LABELS", "(\\Inbox)")

    def move_to_trash(self, uids: Sequence[str]) -> None:
        trash = self.find_trash_folder()
        self._select(INBOX, readonly=False)
        for batch in batches(uids, ACTION_BATCH):
            self._uid("MOVE", ",".join(batch), trash)

    def delete_permanently(self, uids: Sequence[str]) -> None:
        trash = self.find_trash_folder()
        ordered = list(uids)
        message_ids: list[str] = []
        for batch in batches(ordered, ACTION_BATCH):
            self._select(INBOX, readonly=False)
            for line in self._uid("FETCH", ",".join(batch), "(UID X-GM-MSGID)"):
                if line is None:
                    continue
                message_ids.extend(
                    raw.decode("utf-8", errors="replace")
                    for raw in MSGID_PATTERN.findall(line)
                )
        for batch in batches(ordered, ACTION_BATCH):
            self._select(INBOX, readonly=False)
            self._uid("MOVE", ",".join(batch), trash)
        self._select(trash, readonly=False)
        trash_uids: list[str] = []
        for message_id in message_ids:
            for item in self._uid("SEARCH", "X-GM-MSGID", message_id):
                if item:
                    trash_uids.extend(item.decode("ascii", errors="replace").split())
        for batch in batches(trash_uids, ACTION_BATCH):
            self._uid("STORE", ",".join(batch), "+FLAGS", "(\\Deleted)")
        for batch in batches(trash_uids, ACTION_BATCH):
            self._uid("EXPUNGE", ",".join(batch))

    def _open_connection(self) -> ImapConnection:
        try:
            return self._imap_factory(self.host, self.port)
        except OSError as error:
            raise ImapError(
                f"Não foi possível conectar ao Gmail em {self.host}:{self.port}: {error}"
            ) from error

    def _authenticate(self, connection: ImapConnection) -> None:
        try:
            status, data = connection.login(self.address, self._password)
        except imaplib.IMAP4.error as error:
            raise ImapError(f"Login rejeitado pelo Gmail: {error}") from error
        if status != "OK":
            raise ImapError(
                f"Login rejeitado pelo Gmail ({status}): {server_detail(data)}"
            )

    def _require_capabilities(self, connection: ImapConnection) -> None:
        available = fetch_capabilities(connection)
        missing = [name for name in REQUIRED_CAPABILITIES if name not in available]
        if missing:
            raise ImapError(
                "O servidor não anunciou os recursos necessários: "
                + ", ".join(missing)
                + ". Verifique se o IMAP está habilitado na conta do Gmail."
            )

    def _require_connection(self) -> ImapConnection:
        if self.connection is None:
            raise ImapError("Cliente IMAP não conectado: chame connect() antes.")
        return self.connection

    def _select(self, mailbox: str, *, readonly: bool) -> None:
        connection = self._require_connection()
        if self._selected == mailbox and self._readonly == readonly:
            return
        method = connection.examine if readonly else connection.select
        status, data = method(mailbox)
        if status != "OK":
            raise ImapError(
                f"Não foi possível abrir a pasta {mailbox}: {server_detail(data)}"
            )
        self._selected = mailbox
        self._readonly = readonly

    def _uid(self, command: str, *args: str) -> list[bytes | None]:
        connection = self._require_connection()
        status, data = connection.uid(command, *args)
        if status != "OK":
            raise ImapError(f"Falha no comando {command}: {server_detail(data)}")
        return data
