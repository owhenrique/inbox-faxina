from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn

from email_cleanup.config import ACTION_BATCH
from email_cleanup.db import Message, list_messages, record_manifest
from email_cleanup.imap import ImapClient, ImapError
from email_cleanup.review import ACTION_COLORS, format_count, format_size
from email_cleanup.rules import build_plan, pending_by_action

CONFIRM_APPLY = "APLICAR"
CONFIRM_DELETE = "APAGAR DEFINITIVO"
SAMPLE_SIZE = 10
ACTION_ORDER = ("arquivar", "lixeira", "apagar")
ACTION_VERBS = {
    "arquivar": "Arquivando",
    "lixeira": "Movendo para a Lixeira",
    "apagar": "Apagando definitivamente",
}


@dataclass
class ApplyResult:
    done: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def confirmation_phrase(pending: Mapping[str, Sequence[str]]) -> str:
    return CONFIRM_DELETE if "apagar" in pending else CONFIRM_APPLY


def sender_sample(
    messages: Sequence[Message], uids: Sequence[str], limit: int = SAMPLE_SIZE
) -> list[str]:
    wanted = set(uids)
    senders = dict.fromkeys(m.sender for m in messages if m.uid in wanted)
    return list(senders)[:limit]


def execute(
    client: ImapClient,
    database: sqlite3.Connection,
    pending: Mapping[str, Sequence[str]],
    on_batch: Callable[[str, int], None] | None = None,
) -> ApplyResult:
    operations = {
        "arquivar": client.archive,
        "lixeira": client.move_to_trash,
        "apagar": client.delete_permanently,
    }
    result = ApplyResult()
    for action in ACTION_ORDER:
        uids = list(pending.get(action, ()))
        for start in range(0, len(uids), ACTION_BATCH):
            batch = uids[start : start + ACTION_BATCH]
            batch_id = uuid.uuid4().hex
            try:
                operations[action](batch)
            except ImapError as error:
                for uid in batch:
                    record_manifest(database, uid, action, batch_id, "erro")
                result.error = str(error)
                return result
            for uid in batch:
                record_manifest(database, uid, action, batch_id, "ok")
            result.done[action] = result.done.get(action, 0) + len(batch)
            if on_batch is not None:
                on_batch(action, len(batch))
    return result


def preview_panel(
    pending: Mapping[str, Sequence[str]], messages: Sequence[Message]
) -> Panel:
    sizes = {message.uid: message.size for message in messages}
    lines = []
    for action in ACTION_ORDER:
        uids = pending.get(action, ())
        if not uids:
            continue
        color = ACTION_COLORS[action]
        total = sum(sizes.get(uid, 0) for uid in uids)
        sample = ", ".join(sender_sample(messages, uids))
        lines.append(
            f"[{color}]{action}[/{color}]: {format_count(len(uids))} emails • "
            f"{format_size(total)}\n  ex.: {sample}"
        )
    return Panel("\n".join(lines), title="Prévia da aplicação", border_style="magenta")


def run_apply(
    database: sqlite3.Connection,
    console: Console,
    client_factory: Callable[[], ImapClient],
    *,
    dry_run: bool,
    confirm: Callable[[str], str] = input,
    now: datetime | None = None,
) -> int:
    messages = list_messages(database)
    plan = build_plan(database, messages, now or datetime.now(timezone.utc))
    if not plan.actions and not _has_rules(database):
        console.print("[yellow]⚠ Nenhuma regra: rode `email-cleanup review` primeiro.[/yellow]")
        return 1
    pending = pending_by_action(database, plan)
    if not pending:
        console.print("[green]✔[/green] Nada pendente: o plano já foi aplicado.")
        return 0
    console.print(preview_panel(pending, messages))
    if dry_run:
        console.print("[cyan]➜ --dry-run: nada foi enviado à conta.[/cyan]")
        return 0
    phrase = confirmation_phrase(pending)
    try:
        typed = confirm(f"Digite {phrase} para executar: ")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Cancelado; nada foi executado.[/yellow]")
        return 130
    if typed.strip() != phrase:
        console.print("[yellow]Confirmação não confere; nada foi executado.[/yellow]")
        return 1
    return _run_batches(database, console, client_factory, pending)


def _has_rules(database: sqlite3.Connection) -> bool:
    return database.execute("SELECT 1 FROM rules LIMIT 1").fetchone() is not None


def _run_batches(
    database: sqlite3.Connection,
    console: Console,
    client_factory: Callable[[], ImapClient],
    pending: Mapping[str, Sequence[str]],
) -> int:
    total = sum(len(uids) for uids in pending.values())
    client = client_factory()
    progress = Progress(
        TextColumn("[cyan]Aplicando[/cyan]"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    )
    try:
        with progress:
            task = progress.add_task("apply", total=total)
            result = execute(
                client,
                database,
                pending,
                on_batch=lambda action, count: progress.advance(task, count),
            )
    finally:
        client.close()
    for action, count in result.done.items():
        console.print(f"[green]✔[/green] {ACTION_VERBS[action]}: {format_count(count)} emails")
    if result.error is not None:
        console.print(f"[red]⚠ Lote falhou: {result.error}[/red]")
        console.print("Rode `email-cleanup apply` de novo para continuar do que faltou.")
        return 1
    return 0
