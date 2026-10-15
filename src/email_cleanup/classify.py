from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from email_cleanup.config import GROUP_MIN_COUNT, GROUP_MIN_SIZE
from email_cleanup.db import Message

BYTES_PER_MB = 1024 * 1024

PROMOTION_SUBJECT_TERMS = (
    "desconto",
    "oferta",
    "promocao",
    "imperdivel",
    "frete gratis",
    "black friday",
    "sale",
    "deal",
    "newsletter",
    "cupom",
    "liquidacao",
)

PROMOTION_LOCAL_PART_TERMS = (
    "promo",
    "news",
    "ofertas",
    "deals",
    "shop",
    "loja",
)

NOTIFICATION_SUBJECT_TERMS = (
    "fatura",
    "seu pedido",
    "codigo de verificacao",
    "boleto",
    "senha",
)

NOTIFICATION_LOCAL_PART_TERMS = (
    "no-reply",
    "noreply",
    "nao-responda",
    "notification",
    "alert",
    "aviso",
    "mailer-daemon",
)


@dataclass(frozen=True)
class Group:
    sender: str | None
    name: str
    count: int
    total_size: int
    oldest_date: str
    promo_count: int
    notification_count: int
    remainder: bool


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return stripped.lower()


def is_promotion(message: Message) -> bool:
    return _matches(fold(message.subject), PROMOTION_SUBJECT_TERMS) or _matches(
        _local_part(message.sender), PROMOTION_LOCAL_PART_TERMS
    )


def is_notification(message: Message) -> bool:
    return _matches(fold(message.subject), NOTIFICATION_SUBJECT_TERMS) or _matches(
        _local_part(message.sender), NOTIFICATION_LOCAL_PART_TERMS
    )


def is_older_than(internal_date: str, years: int, now: datetime) -> bool:
    return datetime.fromisoformat(internal_date) < now.replace(year=now.year - years)


def is_larger_than(value: int | Message, min_mb: float) -> bool:
    size = value.size if isinstance(value, Message) else value
    return size >= min_mb * BYTES_PER_MB


def build_groups(
    messages: Sequence[Message],
    min_count: int = GROUP_MIN_COUNT,
    min_size: int = GROUP_MIN_SIZE,
) -> list[Group]:
    by_sender: dict[str, list[Message]] = {}
    for message in messages:
        by_sender.setdefault(message.sender, []).append(message)

    groups: list[Group] = []
    remainder_messages: list[Message] = []
    for sender, sender_messages in by_sender.items():
        total_size = sum(message.size for message in sender_messages)
        if len(sender_messages) >= min_count or total_size >= min_size:
            groups.append(_build_group(sender, sender_messages, remainder=False))
        else:
            remainder_messages.extend(sender_messages)

    if remainder_messages:
        groups.append(_build_group(None, remainder_messages, remainder=True))

    groups.sort(key=lambda group: (group.remainder, -group.total_size, group.sender or ""))
    return groups


def _build_group(sender: str | None, messages: Sequence[Message], remainder: bool) -> Group:
    latest = max(messages, key=lambda message: message.internal_date)
    name = "diversos" if remainder else latest.sender_name
    return Group(
        sender=sender,
        name=name,
        count=len(messages),
        total_size=sum(message.size for message in messages),
        oldest_date=min(message.internal_date for message in messages),
        promo_count=sum(1 for message in messages if is_promotion(message)),
        notification_count=sum(1 for message in messages if is_notification(message)),
        remainder=remainder,
    )


def _local_part(sender: str) -> str:
    return fold(sender.split("@", 1)[0])


def _matches(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)
