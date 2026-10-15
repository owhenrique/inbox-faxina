from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn

from .catalog import catalog_messages
from .config import ConfigError, database_path, load_settings
from .db import connect
from .imap import ImapClient, ImapError
from .apply import run_apply
from .review import run_review


class CatalogProgress:
    def __init__(self, console: Console) -> None:
        self._progress = Progress(
            TextColumn("[cyan]{task.description}[/cyan]"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        )
        self._task_id: TaskID | None = None

    def update(self, done: int, total: int) -> None:
        if self._task_id is None:
            self._progress.start()
            self._task_id = self._progress.add_task("Catalogando mensagens", total=total)
        self._progress.update(self._task_id, completed=done)

    def stop(self) -> None:
        if self._task_id is not None:
            self._progress.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-cleanup",
        description="Catálogo e limpeza da caixa de entrada antiga do Gmail (IMAP + SQLite).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="comando", required=True)
    catalog_parser = subparsers.add_parser(
        "catalog",
        help="Catalogar a caixa de entrada no banco local (somente leitura).",
        description=(
            "Lê a INBOX via IMAP em modo somente leitura e grava os metadados "
            "de cada mensagem no banco local, retomando de onde parou."
        ),
    )
    catalog_parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Catalogar no máximo N mensagens nesta execução.",
    )
    catalog_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-baixar tudo desde o início, mesmo o que já está catalogado.",
    )
    catalog_parser.set_defaults(handler=run_catalog)
    review_parser = subparsers.add_parser(
        "review",
        help="Revisar grupos por remetente e criar regras de limpeza (local).",
        description=(
            "Interface interativa: decide ações por grupo ou em massa e grava "
            "regras no banco local, sem tocar na conta."
        ),
    )
    review_parser.set_defaults(handler=run_review_command)
    apply_parser = subparsers.add_parser(
        "apply",
        help="Aplicar as regras na conta, com prévia e confirmação digitada.",
        description=(
            "Resolve o plano a partir das regras, mostra a prévia e só executa "
            "após a confirmação digitada."
        ),
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar a prévia e parar, sem enviar nada à conta.",
    )
    apply_parser.set_defaults(handler=run_apply_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


def run_review_command(args: argparse.Namespace) -> int:
    database = connect(database_path())
    try:
        return run_review(database, Console())
    finally:
        database.close()


def run_apply_command(args: argparse.Namespace) -> int:
    errors = Console(stderr=True)
    database = connect(database_path())

    def open_client() -> ImapClient:
        settings = load_settings()
        client = ImapClient(settings.address, settings.app_password)
        client.connect()
        return client

    try:
        return run_apply(database, Console(), open_client, dry_run=args.dry_run)
    except (ConfigError, ImapError) as error:
        errors.print(f"[red]{error}[/red]")
        return 1
    finally:
        database.close()


def run_catalog(args: argparse.Namespace) -> int:
    console = Console()
    errors = Console(stderr=True)
    if args.limit is not None and args.limit < 0:
        errors.print("[red]--limit precisa ser zero ou positivo.[/red]")
        return 2
    try:
        settings = load_settings()
    except ConfigError as error:
        errors.print(f"[red]{error}[/red]")
        return 1
    path = database_path()
    database = connect(path)
    progress = CatalogProgress(console)
    try:
        client = ImapClient(settings.address, settings.app_password)
        try:
            connection = client.connect()
            cataloged = catalog_messages(
                connection,
                database,
                refresh=args.refresh,
                limit=args.limit,
                on_progress=progress.update,
                account=settings.address,
            )
        except ImapError as error:
            errors.print(f"[red]{error}[/red]")
            return 1
        except KeyboardInterrupt:
            console.print(
                "[yellow]Catálogo interrompido; o que já foi baixado está salvo. "
                "Rode de novo para retomar de onde parou.[/yellow]"
            )
            return 130
        finally:
            progress.stop()
            client.close()
        _print_summary(console, cataloged, database, path)
        return 0
    finally:
        database.close()


def _print_summary(
    console: Console, cataloged: int, database: sqlite3.Connection, path: Path
) -> None:
    console.print(f"[green]✓[/green] Catalogadas nesta execução: [bold]{cataloged}[/bold]")
    console.print(
        f"[green]✓[/green] Total no banco: [bold]{_stored_message_count(database)}[/bold]"
    )
    console.print(f"[green]✓[/green] Banco: {path}")


def _stored_message_count(database: sqlite3.Connection) -> int:
    row = database.execute("SELECT COUNT(*) FROM messages").fetchone()
    return int(row[0])
