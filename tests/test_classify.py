from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from email_cleanup.classify import (
    Group,
    build_groups,
    fold,
    is_larger_than,
    is_notification,
    is_older_than,
    is_promotion,
)
from email_cleanup.db import Message

MB = 1024 * 1024
NEUTRAL_SENDER = "contato@example.com"
NEUTRAL_DATE = "2024-01-01T10:00:00+00:00"
NOW = datetime(2025, 10, 15, 10, 0, 0, tzinfo=timezone.utc)


def make_message(
    uid: str,
    *,
    sender: str = "news@shop.example",
    sender_name: str = "Shop News",
    domain: str = "shop.example",
    subject: str = "Subject",
    internal_date: str = NEUTRAL_DATE,
    size: int = 1024,
    seen: bool = False,
    has_attachment: bool = False,
    labels: tuple[str, ...] = ("\\Inbox",),
    fetched_at: str = "2026-10-15T10:00:00+00:00",
) -> Message:
    return Message(
        uid=uid,
        sender=sender,
        sender_name=sender_name,
        domain=domain,
        subject=subject,
        internal_date=internal_date,
        size=size,
        seen=seen,
        has_attachment=has_attachment,
        labels=labels,
        fetched_at=fetched_at,
    )


def neutral_message(
    uid: str,
    *,
    sender: str = NEUTRAL_SENDER,
    sender_name: str = "Equipe",
    subject: str = "Assunto neutro",
) -> Message:
    return make_message(
        uid,
        sender=sender,
        sender_name=sender_name,
        domain=sender.split("@", 1)[1],
        subject=subject,
    )


def sender_messages(uid_prefix: str, sender: str, *, count: int, size: int, name: str) -> list[Message]:
    return [
        make_message(
            f"{uid_prefix}-{index}",
            sender=sender,
            sender_name=name,
            domain=sender.split("@", 1)[1],
            internal_date=f"2024-01-{index:02d}T10:00:00+00:00",
            size=size,
        )
        for index in range(1, count + 1)
    ]


def test_fold_lowercases() -> None:
    assert fold("OFERTA RELÂMPAGO") == "oferta relampago"


def test_fold_removes_accents() -> None:
    assert fold("Promoção Imperdível") == "promocao imperdivel"
    assert fold("LIQUIDAÇÃO À VISTA") == "liquidacao a vista"


@pytest.mark.parametrize(
    "subject",
    [
        "Desconto de 30%",
        "Oferta especial",
        "Promoção relâmpago",
        "Imperdível: últimas unidades",
        "Frete grátis acima de R$ 99",
        "Black Friday começou",
        "Mega Sale de verão",
        "Newsletter semanal",
        "Deals da semana",
        "Liquidação de inverno",
        "Cupom de R$ 50",
    ],
)
def test_is_promotion_matches_subject_terms(subject: str) -> None:
    assert is_promotion(neutral_message("1", subject=subject))


@pytest.mark.parametrize(
    "sender",
    [
        "promo@example.com",
        "ofertas@example.com",
        "deals@example.com",
        "shop@example.com",
        "loja@example.com",
        "news@example.com",
    ],
)
def test_is_promotion_matches_local_parts(sender: str) -> None:
    assert is_promotion(neutral_message("1", sender=sender))


def test_is_promotion_false_for_regular_message() -> None:
    message = neutral_message(
        "1",
        sender="amigo@example.com",
        sender_name="Amigo",
        subject="Vamos almoçar amanhã?",
    )

    assert not is_promotion(message)


def test_is_promotion_false_for_notification_message() -> None:
    message = neutral_message("1", sender="no-reply@example.com", subject="Sua fatura chegou")

    assert not is_promotion(message)


@pytest.mark.parametrize(
    "subject",
    [
        "Fatura do cartão de outubro",
        "Seu pedido foi enviado",
        "Código de verificação: 987654",
        "Boleto vence em 3 dias",
        "Sua senha foi alterada",
    ],
)
def test_is_notification_matches_subject_terms(subject: str) -> None:
    assert is_notification(neutral_message("1", subject=subject))


@pytest.mark.parametrize(
    "sender",
    [
        "no-reply@example.com",
        "noreply@example.com",
        "nao-responda@example.com",
        "notification@example.com",
        "alert@example.com",
        "aviso@example.com",
        "mailer-daemon@example.com",
    ],
)
def test_is_notification_matches_local_parts(sender: str) -> None:
    assert is_notification(neutral_message("1", sender=sender))


def test_is_notification_false_for_regular_message() -> None:
    message = neutral_message(
        "1",
        sender="amigo@example.com",
        sender_name="Amigo",
        subject="Vamos almoçar amanhã?",
    )

    assert not is_notification(message)


def test_is_notification_false_for_promotion_message() -> None:
    message = neutral_message("1", sender="promo@example.com", subject="Oferta especial")

    assert not is_notification(message)


def test_promotion_and_notification_flags_are_independent() -> None:
    message = neutral_message("1", sender="promo@example.com", subject="Sua fatura chegou")

    assert is_promotion(message)
    assert is_notification(message)


def test_is_older_than_true_for_old_date() -> None:
    assert is_older_than(internal_date="2019-04-01T10:00:00+00:00", years=5, now=NOW)


def test_is_older_than_false_for_recent_date() -> None:
    assert not is_older_than("2024-04-01T10:00:00+00:00", 5, NOW)


def test_is_older_than_false_at_exact_anniversary() -> None:
    assert not is_older_than("2020-10-15T10:00:00+00:00", 5, NOW)


def test_is_older_than_true_one_second_before_anniversary() -> None:
    assert is_older_than("2020-10-15T09:59:59+00:00", 5, NOW)


def test_is_older_than_false_one_second_after_anniversary() -> None:
    assert not is_older_than("2020-10-15T10:00:01+00:00", 5, NOW)


def test_is_larger_than_counts_exact_limit() -> None:
    assert is_larger_than(20 * MB, min_mb=20.0)


def test_is_larger_than_below_limit_does_not_count() -> None:
    assert not is_larger_than(20 * MB - 1, 20.0)


def test_is_larger_than_accepts_message() -> None:
    assert is_larger_than(make_message("1", size=25 * MB), 20.0)


def test_is_larger_than_false_for_small_message() -> None:
    assert not is_larger_than(make_message("1", size=1024), 20.0)


def test_build_groups_empty_input_returns_empty_list() -> None:
    assert build_groups([]) == []


def test_build_groups_sender_with_enough_messages_is_a_group() -> None:
    messages = sender_messages("news", "news@shop.example", count=5, size=1000, name="Shop News") + [
        make_message(
            "amigo-1",
            sender="amigo@example.com",
            sender_name="Amigo",
            domain="example.com",
            internal_date="2024-02-01T10:00:00+00:00",
            size=500,
        ),
        make_message(
            "amigo-2",
            sender="amigo@example.com",
            sender_name="Amigo",
            domain="example.com",
            internal_date="2024-02-02T10:00:00+00:00",
            size=500,
        ),
    ]

    groups = build_groups(messages)

    assert len(groups) == 2
    group, remainder = groups
    assert group.sender == "news@shop.example"
    assert group.name == "Shop News"
    assert group.count == 5
    assert group.total_size == 5000
    assert group.oldest_date == "2024-01-01T10:00:00+00:00"
    assert not group.remainder
    assert remainder.remainder
    assert remainder.sender is None
    assert remainder.name == "diversos"
    assert remainder.count == 2
    assert remainder.total_size == 1000
    assert remainder.oldest_date == "2024-02-01T10:00:00+00:00"


def test_build_groups_sender_at_exact_size_threshold_is_a_group() -> None:
    messages = sender_messages("loja", "loja@example.com", count=4, size=5 * MB, name="Loja")

    groups = build_groups(messages)

    assert len(groups) == 1
    assert groups[0].sender == "loja@example.com"
    assert groups[0].count == 4
    assert groups[0].total_size == 20 * MB
    assert not groups[0].remainder


def test_build_groups_sender_just_below_thresholds_goes_to_remainder() -> None:
    sizes = [5 * MB, 5 * MB, 5 * MB, 5 * MB - 1]
    messages = [
        make_message(
            f"noticias-{index}",
            sender="noticias@example.com",
            sender_name="Notícias",
            domain="example.com",
            internal_date=f"2024-03-{index:02d}T10:00:00+00:00",
            size=size,
        )
        for index, size in enumerate(sizes, start=1)
    ]

    groups = build_groups(messages)

    assert len(groups) == 1
    assert groups[0].remainder
    assert groups[0].count == 4
    assert groups[0].total_size == 20 * MB - 1


def test_build_groups_sorts_by_total_size_desc_then_sender_asc_keeping_remainder_last() -> None:
    messages = (
        sender_messages("alpha", "alpha@example.com", count=5, size=1000, name="Alpha")
        + sender_messages("beta", "beta@example.com", count=5, size=1800, name="Beta")
        + sender_messages("gamma", "gamma@example.com", count=5, size=1800, name="Gamma")
        + sender_messages("delta", "delta@example.com", count=4, size=6000, name="Delta")
    )

    groups = build_groups(messages)

    assert [group.sender for group in groups] == [
        "beta@example.com",
        "gamma@example.com",
        "alpha@example.com",
        None,
    ]
    assert groups[-1].remainder
    assert groups[-1].total_size == 24000


def test_build_groups_merges_senders_below_thresholds_into_one_remainder() -> None:
    messages = sender_messages("alpha", "alpha@example.com", count=1, size=100, name="Alpha") + sender_messages(
        "beta", "beta@example.com", count=1, size=200, name="Beta"
    )

    groups = build_groups(messages)

    assert len(groups) == 1
    assert groups[0].remainder
    assert groups[0].count == 2
    assert groups[0].total_size == 300


def test_build_groups_has_no_remainder_when_every_sender_qualifies() -> None:
    messages = sender_messages("alpha", "alpha@example.com", count=5, size=1000, name="Alpha") + sender_messages(
        "beta", "beta@example.com", count=1, size=25 * MB, name="Beta"
    )

    groups = build_groups(messages)

    assert [group.sender for group in groups] == ["beta@example.com", "alpha@example.com"]
    assert all(not group.remainder for group in groups)


def test_build_groups_aggregates_counts_and_uses_most_recent_sender_name() -> None:
    messages = [
        make_message(
            "1",
            sender=NEUTRAL_SENDER,
            sender_name="Old Name",
            domain="example.com",
            subject="Desconto de 30%",
            internal_date="2024-01-01T10:00:00+00:00",
            size=1000,
        ),
        make_message(
            "2",
            sender=NEUTRAL_SENDER,
            sender_name="Old Name",
            domain="example.com",
            subject="Oferta relâmpago",
            internal_date="2024-01-02T10:00:00+00:00",
            size=1000,
        ),
        make_message(
            "3",
            sender=NEUTRAL_SENDER,
            sender_name="Old Name",
            domain="example.com",
            subject="Sua fatura chegou",
            internal_date="2024-01-03T10:00:00+00:00",
            size=1000,
        ),
        make_message(
            "4",
            sender=NEUTRAL_SENDER,
            sender_name="Middle Name",
            domain="example.com",
            subject="Código de verificação",
            internal_date="2024-01-04T10:00:00+00:00",
            size=1000,
        ),
        make_message(
            "5",
            sender=NEUTRAL_SENDER,
            sender_name="New Name",
            domain="example.com",
            subject="Assunto neutro",
            internal_date="2024-01-05T10:00:00+00:00",
            size=1000,
        ),
    ]

    groups = build_groups(messages)

    assert len(groups) == 1
    group = groups[0]
    assert group.name == "New Name"
    assert group.count == 5
    assert group.total_size == 5000
    assert group.oldest_date == "2024-01-01T10:00:00+00:00"
    assert group.promo_count == 2
    assert group.notification_count == 2
    assert not group.remainder


def test_build_groups_accepts_custom_thresholds() -> None:
    messages = sender_messages("alpha", "alpha@example.com", count=2, size=600, name="Alpha")

    groups = build_groups(messages, min_count=2, min_size=10_000)

    assert len(groups) == 1
    assert groups[0].sender == "alpha@example.com"
    assert not groups[0].remainder


def test_build_groups_keeps_sender_below_custom_thresholds_in_remainder() -> None:
    messages = sender_messages("alpha", "alpha@example.com", count=2, size=100, name="Alpha")

    groups = build_groups(messages, min_count=5, min_size=1000)

    assert len(groups) == 1
    assert groups[0].remainder


def test_group_is_a_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Group)

    group = build_groups([make_message("1")])[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        group.count = 2


def test_is_financial_matches_bank_sender_and_subject() -> None:
    from email_cleanup.classify import is_financial

    assert is_financial(neutral_message("1", sender="noreply@nubank.com.br", subject="Sua fatura"))
    assert is_financial(neutral_message("2", subject="Extrato de março"))
    assert not is_financial(neutral_message("3", subject="Olá, tudo bem?"))


def test_is_security_matches_codes_and_resets() -> None:
    from email_cleanup.classify import is_security

    assert is_security(neutral_message("1", subject="Seu código de verificação"))
    assert is_security(neutral_message("2", subject="Redefinição de senha"))
    assert is_security(neutral_message("3", subject="Security alert"))
    assert not is_security(neutral_message("4", subject="Promoção da semana"))


def test_is_personal_requires_webmail_domain_and_non_automatic_sender() -> None:
    from email_cleanup.classify import is_personal

    assert is_personal(neutral_message("1", sender="maria@gmail.com", subject="Oi"))
    assert is_personal(neutral_message("2", sender="joao@hotmail.com", subject="Oi"))
    assert not is_personal(neutral_message("3", sender="noreply@gmail.com", subject="Oi"))
    assert not is_personal(neutral_message("4", sender="maria@empresa.com.br", subject="Oi"))


def test_is_document_by_attachment_or_subject() -> None:
    from email_cleanup.classify import is_document

    assert is_document(make_message("1", has_attachment=True))
    assert is_document(neutral_message("2", subject="Comprovante de pagamento"))
    assert not is_document(neutral_message("3", subject="Olá"))


def test_sensitivity_reasons_ordered_and_empty_when_clean() -> None:
    from email_cleanup.classify import sensitivity_reasons

    message = make_message(
        "1",
        sender="maria@gmail.com",
        domain="gmail.com",
        subject="Boleto em anexo",
        has_attachment=True,
    )
    assert sensitivity_reasons(message) == ("financeiro", "pessoal", "documento")
    assert sensitivity_reasons(neutral_message("2")) == ()


def test_group_counts_sensitive_messages_and_reasons() -> None:
    messages = [
        neutral_message(str(i), sender="maria@gmail.com", subject="Oi") for i in range(3)
    ] + [
        neutral_message(str(i), sender="maria@gmail.com", subject="Boleto") for i in range(3, 5)
    ]
    groups = build_groups(messages, min_count=1)
    assert groups[0].sensitive_count == 5
    assert groups[0].sensitive_reasons[0] == "pessoal"
    assert set(groups[0].sensitive_reasons) == {"pessoal", "financeiro"}


def test_group_without_sensitive_messages_is_clean() -> None:
    groups = build_groups([neutral_message("1")], min_count=1)
    assert groups[0].sensitive_count == 0
    assert groups[0].sensitive_reasons == ()


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        ("contato@pixar.com", "Novidades"),
        ("lojas@pixel.com", "Pixel 9 chegou"),
        ("hello@mail.com", "Bank of Culture"),
        ("ofertas@loja.com", "Saldo de ofertas"),
        ("oi@internacional.com.br", "Olá"),
    ],
)
def test_is_financial_ignores_prefix_and_ambiguous_matches(sender: str, subject: str) -> None:
    from email_cleanup.classify import is_financial

    assert not is_financial(neutral_message("1", sender=sender, subject=subject))


@pytest.mark.parametrize(
    ("sender", "subject"),
    [
        ("noreply@nubank.com.br", "Olá"),
        ("noreply@itau.com.br", "Olá"),
        ("no-reply@bradesco.com.br", "Olá"),
        ("no-reply@santander.com.br", "Olá"),
        ("service@paypal.com", "Olá"),
        ("no-reply@mercadopago.com", "Olá"),
        ("no-reply@bancointer.com.br", "Olá"),
        ("news@interbank.com", "Olá"),
        ("no-reply@inter.co", "Olá"),
        ("no-reply@bancodobrasil.com.br", "Olá"),
        ("x@example.com", "Pix recebido"),
        ("x@example.com", "Suas faturas"),
        ("x@example.com", "Seu saldo disponível"),
        ("x@example.com", "Banco Inter: novidades"),
        ("x@example.com", "Online banking"),
    ],
)
def test_is_financial_matches_known_institutions_and_terms(sender: str, subject: str) -> None:
    from email_cleanup.classify import is_financial

    assert is_financial(neutral_message("1", sender=sender, subject=subject))


@pytest.mark.parametrize(
    "subject",
    ["Confirme seu pedido", "Login social", "Acesso liberado", "Promoção da semana"],
)
def test_is_security_ignores_broad_terms(subject: str) -> None:
    from email_cleanup.classify import is_security

    assert not is_security(neutral_message("1", subject=subject))


@pytest.mark.parametrize(
    "subject",
    [
        "Novo login detectado",
        "Login suspeito na sua conta",
        "New login to your account",
        "New sign-in",
        "Confirme seu e-mail",
        "Confirme seu cadastro",
        "Confirm your account",
        "Acesso não reconhecido",
        "Novo acesso à conta",
        "Acesso a sua conta",
        "Redefina sua senha",
        "Redefinir senha",
        "Reset password",
        "Reset your password",
        "Password reset requested",
    ],
)
def test_is_security_matches_specific_phrases(subject: str) -> None:
    from email_cleanup.classify import is_security

    assert is_security(neutral_message("1", subject=subject))


def test_is_personal_excludes_promotion_local_parts() -> None:
    from email_cleanup.classify import is_personal

    assert not is_personal(neutral_message("1", sender="news@gmail.com", subject="Oi"))
    assert not is_personal(neutral_message("2", sender="promo@hotmail.com", subject="Oi"))
    assert is_personal(neutral_message("3", sender="maria@gmail.com", subject="Fatura da semana"))
    assert is_personal(neutral_message("4", sender="maria@gmail.com", subject="Oi"))


def test_group_sensitive_reasons_tie_breaks_by_reason_order() -> None:
    messages = [
        neutral_message("1", sender="maria@gmail.com", subject="Boleto"),
        neutral_message("2", sender="maria@gmail.com", subject="Contrato"),
    ]
    groups = build_groups(messages, min_count=1)
    assert groups[0].sensitive_reasons == ("financeiro", "pessoal", "documento")


def test_group_sensitive_reasons_sorted_by_count_desc() -> None:
    messages = [
        neutral_message(str(i), sender="maria@gmail.com", subject="Contrato") for i in range(3)
    ] + [neutral_message("9", sender="maria@gmail.com", subject="Boleto")]
    groups = build_groups(messages, min_count=1)
    assert groups[0].sensitive_reasons == ("pessoal", "documento", "financeiro")
