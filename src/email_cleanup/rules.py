from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from email_cleanup.classify import is_larger_than, is_notification, is_older_than, is_promotion
from email_cleanup.db import Message, add_rule, list_rules, succeeded_uids

ACTIONS = ("manter", "arquivar", "lixeira", "apagar")
KEEP_ACTION = "manter"
FILTER_KEYS = ("sender", "senders", "domain", "category", "older_than_years", "min_size_mb")
CATEGORY_CHECKS = {"promocao": is_promotion, "notificacao": is_notification}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Resolution:
    action: str
    rule_id: int


@dataclass(frozen=True)
class ActionTotal:
    count: int = 0
    size: int = 0


@dataclass(frozen=True)
class Plan:
    actions: dict[str, Resolution]
    conflicts: dict[str, tuple[int, ...]]
    totals: dict[str, ActionTotal]


def matches(rule_filter: Mapping[str, Any], message: Message, now: datetime) -> bool:
    checks = {
        "sender": lambda value: message.sender == value,
        "senders": lambda value: message.sender in value,
        "domain": lambda value: message.domain == value,
        "category": lambda value: CATEGORY_CHECKS[value](message),
        "older_than_years": lambda value: is_older_than(message.internal_date, value, now),
        "min_size_mb": lambda value: is_larger_than(message, value),
    }
    return all(checks[key](value) for key, value in rule_filter.items())


def save_rule(
    connection: sqlite3.Connection,
    action: str,
    rule_filter: Mapping[str, Any],
    description: str,
) -> int:
    if action not in ACTIONS:
        raise RuleError(f"Ação desconhecida: {action}")
    if not rule_filter:
        raise RuleError("A regra precisa de ao menos um filtro.")
    unknown = set(rule_filter) - set(FILTER_KEYS)
    if unknown:
        raise RuleError(f"Filtro desconhecido: {', '.join(sorted(unknown))}")
    if rule_filter.get("category", "promocao") not in CATEGORY_CHECKS:
        raise RuleError(f"Categoria desconhecida: {rule_filter['category']}")
    return add_rule(connection, action, json.dumps(dict(rule_filter), sort_keys=True), description)


def build_plan(
    connection: sqlite3.Connection, messages: Sequence[Message], now: datetime
) -> Plan:
    rules = [(rule, json.loads(rule.filter_json)) for rule in list_rules(connection)]
    actions: dict[str, Resolution] = {}
    hits: dict[str, list[int]] = {}
    sizes = {message.uid: message.size for message in messages}

    for message in messages:
        for rule, rule_filter in rules:
            if matches(rule_filter, message, now):
                hits.setdefault(message.uid, []).append(rule.id)
                actions[message.uid] = Resolution(rule.action, rule.id)

    totals = {action: ActionTotal() for action in ACTIONS}
    for uid, resolution in actions.items():
        current = totals[resolution.action]
        totals[resolution.action] = ActionTotal(current.count + 1, current.size + sizes[uid])

    conflicts = {uid: tuple(ids) for uid, ids in hits.items() if len(ids) > 1}
    return Plan(actions=actions, conflicts=conflicts, totals=totals)


def rule_hit_counts(
    connection: sqlite3.Connection, messages: Sequence[Message], now: datetime
) -> dict[int, int]:
    rules = [(rule, json.loads(rule.filter_json)) for rule in list_rules(connection)]
    return {
        rule.id: sum(1 for message in messages if matches(rule_filter, message, now))
        for rule, rule_filter in rules
    }


def pending_by_action(
    connection: sqlite3.Connection, plan: Plan
) -> dict[str, list[str]]:
    pending: dict[str, list[str]] = {}
    for action in ACTIONS:
        if action == KEEP_ACTION:
            continue
        done = succeeded_uids(connection, action)
        uids = [
            uid
            for uid, resolution in plan.actions.items()
            if resolution.action == action and uid not in done
        ]
        if uids:
            pending[action] = uids
    return pending
