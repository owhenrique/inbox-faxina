from __future__ import annotations

import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timezone

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from email_cleanup.classify import Group, build_groups, is_notification, is_promotion
from email_cleanup.db import Message, get_meta, list_messages, list_rules, remove_rule
from email_cleanup.rules import ACTIONS, Plan, build_plan, matches, rule_hit_counts, save_rule

PAGE_SIZE = 25
BYTES_PER_MB = 1024 * 1024
STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
        ("instruction", "fg:#808080"),
    ]
)
ACTION_COLORS = {
    "manter": "green",
    "arquivar": "cyan",
    "lixeira": "yellow",
    "apagar": "red",
}
ACTION_DESCRIPTIONS = {
    "manter": "Manter — fica na caixa de entrada, vence regras anteriores",
    "arquivar": "Arquivar — sai da caixa, continua na conta",
    "lixeira": "Lixeira — vai para a Lixeira do Gmail (recuperável por 30 dias)",
    "apagar": "Apagar definitivo — irreversível, esvazia a Lixeira",
}
MENU = (
    ("groups", "Decidir grupos da página"),
    ("bulk", "Atalho em massa"),
    ("rules", "Ver regras"),
    ("remove", "Remover regra"),
    ("preview", "Prévia do plano"),
    ("done", "Concluir"),
)
BULK_OPTIONS = (
    ("promocao", "Promoções"),
    ("notificacao", "Notificações automáticas"),
    ("age", "Mais antigos que N anos"),
    ("size", "Maiores que X MB"),
    ("sender", "Remetente ou domínio"),
)
CONFIRM_DELETE = "APAGAR"


class ReviewAborted(Exception):
    pass


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_count(count: int) -> str:
    return f"{count:,}".replace(",", ".")


def group_description(group: Group) -> str:
    promo = round(100 * group.promo_count / group.count)
    return (
        f"{format_count(group.count)} emails • {format_size(group.total_size)} • "
        f"{promo}% promoções"
    )


def summary_panel(
    account: str | None, messages: Sequence[Message], groups: Sequence[Group], finished_at: str | None
) -> Panel:
    promos = sum(1 for message in messages if is_promotion(message))
    notifications = sum(1 for message in messages if is_notification(message))
    oldest = min((message.internal_date for message in messages), default="—")
    body = "\n".join(
        [
            f"Conta: [bold]{account or '—'}[/bold]",
            f"Mensagens: [bold]{format_count(len(messages))}[/bold] "
            f"({format_size(sum(message.size for message in messages))})",
            f"Grupos: [bold]{len(groups)}[/bold]",
            f"Promoções: {format_count(promos)} • Notificações: {format_count(notifications)}",
            f"Mais antigo: {oldest[:10]}",
            f"Catalogado em: {(finished_at or '—')[:19]}",
        ]
    )
    return Panel(body, title="Revisão da caixa de entrada", border_style="cyan")


def group_actions(groups: Sequence[Group], messages: Sequence[Message], plan: Plan) -> dict[int, str]:
    sender_actions: dict[str, set[str]] = {}
    remainder_senders = {group.sender for group in groups if not group.remainder}
    for message in messages:
        key = message.sender if message.sender in remainder_senders else ""
        resolution = plan.actions.get(message.uid)
        sender_actions.setdefault(key, set()).add(resolution.action if resolution else "—")
    result: dict[int, str] = {}
    for index, group in enumerate(groups):
        actions = sender_actions.get(group.sender or "", {"—"})
        result[index] = actions.pop() if len(actions) == 1 else "misto"
    return result


def groups_table(
    groups: Sequence[Group], actions: dict[int, str], page: int, now: datetime
) -> Table:
    table = Table(title=f"Grupos — página {page + 1}", header_style="bold")
    for column in ("#", "Remetente", "Emails", "Tamanho", "Mais antigo", "Ação"):
        table.add_column(column)
    start = page * PAGE_SIZE
    for index in range(start, min(start + PAGE_SIZE, len(groups))):
        group = groups[index]
        action = actions.get(index, "—")
        color = ACTION_COLORS.get(action, "dim")
        mark = "✔ " if action != "—" else ""
        table.add_row(
            str(index + 1),
            group.sender or "diversos",
            format_count(group.count),
            format_size(group.total_size),
            group.oldest_date[:10],
            f"[{color}]{mark}{action}[/{color}]",
        )
    return table


def plan_panel(plan: Plan) -> Panel:
    lines = [
        f"[{ACTION_COLORS[action]}]{action}[/{ACTION_COLORS[action]}]: "
        f"{format_count(plan.totals[action].count)} emails • "
        f"{format_size(plan.totals[action].size)}"
        for action in ACTIONS
    ]
    lines.append(f"Conflitos (UIDs em mais de uma regra): {len(plan.conflicts)}")
    return Panel("\n".join(lines), title="Prévia do plano", border_style="magenta")


def _ask(question: questionary.Question) -> object:
    answer = question.ask()
    if answer is None:
        raise ReviewAborted
    return answer


def _choose_action() -> str:
    choices = [questionary.Choice(ACTION_DESCRIPTIONS[action], value=action) for action in ACTIONS]
    return str(_ask(questionary.select("O que fazer?", choices=choices, style=STYLE)))


def _confirm_rule(
    action: str, messages: Sequence[Message], description: str
) -> bool:
    count = len(messages)
    size = sum(message.size for message in messages)
    if count == 0:
        Console().print("[yellow]⚠ Nenhuma mensagem atingida; regra não criada.[/yellow]")
        return False
    Console().print(
        f"➜ {description}: [bold]{format_count(count)}[/bold] emails • {format_size(size)}"
    )
    if action == "apagar":
        typed = _ask(questionary.text(f"Digite {CONFIRM_DELETE} para confirmar:", style=STYLE))
        return str(typed).strip() == CONFIRM_DELETE
    return bool(_ask(questionary.confirm("Criar esta regra?", default=True, style=STYLE)))


def _decide_groups(
    connection: sqlite3.Connection,
    messages: Sequence[Message],
    groups: Sequence[Group],
    page: int,
) -> None:
    window = groups[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    if not window:
        return
    choices = [
        questionary.Choice(
            f"{group.sender or 'diversos'} — {group_description(group)}", value=group
        )
        for group in window
    ]
    selected: list[Group] = _ask(  # type: ignore[assignment]
        questionary.checkbox(
            "Selecione os grupos (espaço marca, enter confirma):", choices=choices, style=STYLE
        )
    )
    if not selected:
        return
    action = _choose_action()
    senders = sorted({sender for group in selected if group.sender for sender in [group.sender]})
    if any(group.remainder for group in selected):
        kept = {group.sender for group in groups if not group.remainder}
        senders += sorted({message.sender for message in messages} - kept)
    hit = [message for message in messages if message.sender in set(senders)]
    description = f"{action} {len(selected)} grupo(s) por remetente"
    if _confirm_rule(action, hit, description):
        save_rule(connection, action, {"senders": senders}, description)


def _bulk_filter() -> tuple[dict[str, object], str]:
    kind = str(
        _ask(
            questionary.select(
                "Atalho em massa:",
                choices=[questionary.Choice(label, value=key) for key, label in BULK_OPTIONS],
                style=STYLE,
            )
        )
    )
    if kind in ("promocao", "notificacao"):
        return {"category": kind}, f"categoria {kind}"
    if kind == "age":
        years = int(str(_ask(questionary.text("Quantos anos?", default="5", style=STYLE))))
        return {"older_than_years": years}, f"mais antigos que {years} anos"
    if kind == "size":
        size = float(str(_ask(questionary.text("Quantos MB?", default="5", style=STYLE))))
        return {"min_size_mb": size}, f"maiores que {size:g} MB"
    value = str(_ask(questionary.text("Remetente ou domínio:", style=STYLE))).strip().lower()
    key = "sender" if "@" in value else "domain"
    return {key: value}, f"{key} {value}"


def _bulk(connection: sqlite3.Connection, messages: Sequence[Message], now: datetime) -> None:
    try:
        rule_filter, label = _bulk_filter()
    except ValueError:
        Console().print("[red]Valor inválido.[/red]")
        return
    hit = [message for message in messages if matches(rule_filter, message, now)]
    action = _choose_action()
    description = f"{action}: {label}"
    if _confirm_rule(action, hit, description):
        save_rule(connection, action, rule_filter, description)


def _show_rules(console: Console, connection: sqlite3.Connection, messages: Sequence[Message], now: datetime) -> None:
    hits = rule_hit_counts(connection, messages, now)
    table = Table(title="Regras", header_style="bold")
    for column in ("id", "ação", "descrição", "UIDs atingidos"):
        table.add_column(column)
    for rule in list_rules(connection):
        color = ACTION_COLORS.get(rule.action, "white")
        table.add_row(
            str(rule.id),
            f"[{color}]{rule.action}[/{color}]",
            rule.description,
            format_count(hits.get(rule.id, 0)),
        )
    console.print(table)


def _remove_rule(connection: sqlite3.Connection) -> None:
    rules = list_rules(connection)
    if not rules:
        Console().print("[yellow]Nenhuma regra para remover.[/yellow]")
        return
    choices = [
        questionary.Choice(f"#{rule.id} {rule.action} — {rule.description}", value=rule.id)
        for rule in rules
    ]
    rule_id = _ask(questionary.select("Remover qual regra?", choices=choices, style=STYLE))
    remove_rule(connection, int(rule_id))  # type: ignore[call-overload]


def run_review(connection: sqlite3.Connection, console: Console) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        console.print("[red]A revisão precisa de um terminal interativo (TTY).[/red]")
        return 1
    messages = list_messages(connection)
    if not messages:
        console.print("[yellow]Catálogo vazio: rode `email-cleanup catalog` primeiro.[/yellow]")
        return 1
    groups = build_groups(messages)
    page = 0
    pages = max(1, -(-len(groups) // PAGE_SIZE))
    console.print(
        summary_panel(
            get_meta(connection, "account"),
            messages,
            groups,
            get_meta(connection, "catalog_finished_at"),
        )
    )
    try:
        while True:
            now = datetime.now(timezone.utc)
            plan = build_plan(connection, messages, now)
            console.print(groups_table(groups, group_actions(groups, messages, plan), page, now))
            choice = str(
                _ask(
                    questionary.select(
                        f"Menu (página {page + 1}/{pages}):",
                        choices=[questionary.Choice(label, value=key) for key, label in MENU]
                        + [
                            questionary.Choice("Próxima página", value="next"),
                            questionary.Choice("Página anterior", value="prev"),
                        ],
                        style=STYLE,
                    )
                )
            )
            if choice == "done":
                break
            if choice == "next":
                page = min(page + 1, pages - 1)
            elif choice == "prev":
                page = max(page - 1, 0)
            elif choice == "groups":
                _decide_groups(connection, messages, groups, page)
            elif choice == "bulk":
                _bulk(connection, messages, now)
            elif choice == "rules":
                _show_rules(console, connection, messages, now)
            elif choice == "remove":
                _remove_rule(connection)
            elif choice == "preview":
                console.print(plan_panel(plan))
    except (ReviewAborted, KeyboardInterrupt):
        console.print("[yellow]Revisão encerrada; as regras criadas foram mantidas.[/yellow]")
        return 130
    console.print(plan_panel(build_plan(connection, messages, datetime.now(timezone.utc))))
    console.print("[green]✔[/green] Rode `email-cleanup apply --dry-run` para ver a prévia.")
    return 0
