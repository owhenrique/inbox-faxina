---
id: 2026-10-15-sensitive-flag
card: —
titulo: Sinalizador ⚠ de mensagens sensíveis (perigosas de apagar) na revisão
status: implementada
modo: direto
criada_em: 2026-10-15
implementada_em: 2026-10-15
verificada_em:
branch: feat/sensitive-flag
merge_request:
---

# 2026-10-15-sensitive-flag — Sinalizador de mensagens sensíveis

## Contexto

Na revisão (`review`) o usuário decide por grupo de remetente. Hoje nada
distingue um grupo de promoções de um com emails de banco, códigos de
verificação, pessoas ou documentos. Apagar o grupo errado é irreversível.
Depende da spec [2026-10-15-email-cleanup](2026-10-15-email-cleanup.md) (fechada).

## Objetivo

Marcar com ícone ⚠ e motivo, na UI da revisão, grupos e atalhos em massa que
contenham mensagens provavelmente sensíveis, para o usuário decidir com
consciência. É só aviso: não bloqueia nem muda o fluxo.

## Escopo

**Dentro:**
- Heurística de sensibilidade por mensagem, com motivos nomeados.
- ⚠ na tabela de grupos, na lista de seleção (checkbox) e na confirmação de
  atalhos em massa / regra por grupo.
- Contagem "⚠ N de M" por grupo e por regra (sem limiar: sempre mostra N/M).

**Fora:**
- Bloquear ou exigir confirmação extra (decidido: só avisar).
- Alterar `apply`, manifest, regras persistidas ou schema do SQLite.
- Nova ação ou filtro de regra por sensibilidade (pode virar spec futura).
- ML/leitura do corpo da mensagem (só remetente, assunto, anexo).

## Decisões e respostas do usuário

- P: O que é "perigoso de apagar"? → R: **financeiro/banco, segurança/contas, pessoal (webmail), documentos/anexos** (todos).
- P: Onde aparece o ícone? → R: **tabela de grupos, lista de seleção, atalhos em massa**.
- P: Efeito? → R: **só avisar**.
- P: Regra do grupo? → R: **mostrar contagem** (⚠ N de M), sem limiar.

## Premissas

- O aviso na confirmação vale para ações `lixeira` e `apagar` (as que removem
  da caixa de forma perigosa); `arquivar`/`manter` mostram só a contagem
  informativa já existente. Ajustável em uma constante.
- "Pessoal" = remetente em domínio de webmail pessoal e que não pareça
  automático (`no-reply` etc. não conta como pessoal).
- Anexo continua sendo a heurística existente (`BODYSTRUCTURE`); pode gerar
  falsos positivos, o que é aceitável para um aviso.

## Design técnico

### `classify.py` (funções puras, listas fáceis de calibrar)

- `SENSITIVE_REASONS = ("financeiro", "seguranca", "pessoal", "documento")`.
- `is_financial(message)`: remetente/assunto, via `fold()`, com termos casados
  como palavra inteira (fronteira dos dois lados, plural simples aceito):
  fatura, boleto, extrato, cartão, pix, transferência, "seu saldo"/"saldo
  disponível", imposto, receita federal, nota fiscal, fgts, banco inter,
  banco do brasil, bancodobrasil, bancointer, interbank, inter.co, online
  banking, nubank, itau, bradesco, santander, caixa economica, mercado pago,
  paypal, invoice, payment, pagamento… Termos genéricos soltos ("bank",
  "banco", "saldo") não entram, para evitar `pixar.com` ou "saldo de ofertas".
- `is_security(message)`: assunto com frases específicas: código de
  verificação, verification code, novo login, login suspeito, new login,
  sign-in, 2fa, autenticação, alerta de segurança, security alert, confirme
  seu e-mail/email/cadastro, confirm your email/account, acesso não
  reconhecido, novo acesso, acesso a sua conta, redefinição de senha,
  redefinir/redefina (sua) senha, reset password, reset your password,
  password reset. "Login", "acesso" e "confirme seu" soltos não casam.
- `is_personal(message)`: `message.domain` em `PERSONAL_DOMAINS` (gmail.com,
  googlemail.com, outlook.com, hotmail.com, live.com, msn.com, yahoo.com,
  yahoo.com.br, icloud.com, me.com, proton.me, protonmail.com, uol.com.br,
  bol.com.br, terra.com.br) **e** local-part do remetente sem termos de
  `NOTIFICATION_LOCAL_PART_TERMS` (no-reply, noreply, notification…) nem de
  `PROMOTION_LOCAL_PART_TERMS` (news, promo, deals…).
- `is_document(message)`: `has_attachment` **ou** assunto com contrato,
  recibo, comprovante, passagem, nota fiscal, apólice, declaração, boarding.
- `sensitivity_reasons(message) -> tuple[str, ...]` na ordem de
  `SENSITIVE_REASONS`; vazio = não sensível.
- `Group` ganha `sensitive_count: int` e `sensitive_reasons: tuple[str, ...]`
  (motivos presentes no grupo, ordenados por frequência).

### `review.py`

- Constante `DANGER_ICON = "⚠"`, `DANGER_ACTIONS = ("lixeira", "apagar")`.
- Tabela de grupos: nova coluna `⚠` com `⚠ N/M` (vermelho/amarelo) ou vazio.
- `group_description`: acrescenta ` • ⚠ N sensíveis (financeiro, pessoal)`
  quando N > 0.
- `_confirm_rule`: calcula `sensitivity_reasons` das mensagens atingidas; se
  houver e a ação ∈ `DANGER_ACTIONS`, imprime
  `⚠ N de M mensagens parecem sensíveis (financeiro: a, seguranca: b…)`
  antes da confirmação existente. Não muda a confirmação.
- Painel-resumo: linha `⚠ Sensíveis: N` (total do catálogo).

## Arquivos que a mudança toca

- `src/email_cleanup/classify.py` — editar
- `src/email_cleanup/review.py` — editar
- `tests/test_classify.py` — editar
- `README.md` — editar (seção sobre o ⚠)
- `docs/specs/2026-10-15-sensitive-flag.md` — criar

## Triagem de implementação

| Métrica | Valor |
| --- | --- |
| Tarefas | 4 |
| Arquivos que a mudança toca | 5 |
| Linhas de lógica estimadas | ≈150 |
| Toca em tela (E2E) | Não (TUI) |
| Migração de dados | Não |
| Muda contrato de API | Não |
| Dependência nova | Não |
| Repos envolvidos | 1 |

**Modo sugerido:** direto — poucas tarefas, mudança contida em dois módulos.

## Tarefas

- [x] 1. Testes (falhando) de `is_financial`/`is_security`/`is_personal`/`is_document`, `sensitivity_reasons` e dos novos campos de `Group`.
- [x] 2. Implementar as heurísticas e os campos em `classify.py`.
- [x] 3. `review.py`: coluna ⚠ na tabela, descrição no checkbox, aviso em `_confirm_rule`, linha no painel-resumo.
- [x] 4. README: documentar o ⚠ e como calibrar as listas.

## Critérios de aceite

- [ ] Dado um email de `noreply@nubank.com.br` com assunto "Sua fatura", então `sensitivity_reasons` contém `financeiro`.
- [ ] Dado "Seu código de verificação", então contém `seguranca`.
- [ ] Dado `maria@gmail.com` com assunto comum, então contém `pessoal`; dado `noreply@gmail.com`, então **não** contém `pessoal`.
- [ ] Dado `has_attachment=True` ou assunto "Comprovante de pagamento", então contém `documento`.
- [ ] Dado um grupo com 3 de 10 mensagens sensíveis, então a tabela mostra `⚠ 3/10` e o checkbox lista os motivos.
- [ ] Dado um grupo sem mensagens sensíveis, então nenhum ⚠ aparece.
- [ ] Dada regra `lixeira` ou `apagar` que atinge mensagens sensíveis, então a confirmação mostra o aviso ⚠ com contagem por motivo e o fluxo segue igual.
- [ ] Dada regra `arquivar`, então não aparece o aviso extra.
- [ ] Nenhum schema, regra persistida ou comando do `apply` muda; testes anteriores seguem verdes.

## Telas e fluxos E2E (Playwright)

Não se aplica — TUI de terminal.

## Testes

- Automatizados: `uv run pytest` — heurísticas puras (positivos, negativos,
  acentos via `fold`), agregação em `Group`, e funções puras de formatação do
  ⚠ no review (descrição do grupo e texto do aviso extraídos em funções puras
  testáveis).
- Manual: `review` sobre o catálogo real; conferir grupos de banco/pessoas com
  ⚠ e calibrar as listas se houver ruído demais.

## Verificação independente

Preenchido no `/pato/verify`.

## Registro de testes

Preenchido no `/pato/close-spec`.

## Riscos e pontos em aberto

- Falsos positivos: anexo e "login/acesso" são amplos; aceitável por ser só
  aviso, mas pode poluir a tabela. Calibrar com o catálogo real.
- Falsos negativos: remetente corporativo sensível sem termos conhecidos não
  será marcado; ⚠ ausente não significa seguro.
- Domínios pessoais vão além de gmail.com por decisão desta spec; lista
  editável em `classify.py`.
