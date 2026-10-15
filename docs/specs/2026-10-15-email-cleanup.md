---
id: 2026-10-15-email-cleanup
card: —
titulo: Limpeza da caixa de entrada antiga do Gmail com catálogo e revisão interativa
status: fechada
modo: agent-driven
criada_em: 2026-10-15
implementada_em: 2026-10-15
verificada_em: 2026-10-15
branch: feat/email-cleanup
merge_request:
---

# 2026-10-15-email-cleanup — Limpeza da caixa de entrada do Gmail

## Contexto

Caixa de entrada de Gmail antiga com ~11k mensagens acumuladas. Não dá para
decidir email a email, nem apagar às cegas. O usuário quer um script Python
que primeiro **catalogue** tudo (remetente, data, tamanho, categoria provável,
anexo) e depois deixe **decidir o que limpar por grupo**, antes de qualquer
ação no servidor.

Repo novo, fora do workspace Clanada: `~/Projects/email-cleanup`.

## Objetivo

CLI em Python (`catalog` → `review` → `apply`) que cataloga a INBOX do Gmail
via IMAP, agrupa por remetente para revisão interativa, grava decisões de
limpeza e aplica somente após prévia e confirmação explícita.

## Escopo

**Dentro:**
- Catálogo da INBOX apenas, via IMAP (`imap.gmail.com:993`), com retomada.
- Persistência local em SQLite (sem estourar memória: lotes + cursor).
- Revisão interativa com UI caprichada (rich + questionary): painel-resumo,
  tabela paginada colorida, seleção de grupos por checkbox (navegar com
  setas, marcar com espaço, confirmar com enter), descrições por opção e
  confirmações em PT-BR.
- Ações: manter (explícito, como override), arquivar, mover para a Lixeira,
  apagar definitivamente.
- Prévia (dry-run), confirmações e manifest de auditoria por UID.

**Fora:**
- Spam, Lixeira, arquivados (All Mail), pastas/labels de usuário.
- Enviar, responder, mover para labels específicas, regras automáticas no Gmail.
- Múltiplas contas; OAuth (é senha de app).
- Interface web/tela (TUI de terminal; Playwright não se aplica).

## Decisões e respostas do usuário

- P: Onde mora? → R: repo novo fora do Clanada, `~/Projects/email-cleanup` (git próprio, `docs/specs/`).
- P: Provedor/autenticação? → R: Gmail via IMAP + **senha de app** (2FA).
- P: Como perguntar o que limpar? → R: catálogo **agrupado por remetente** + decisão por grupo.
- P: Ações permitidas? → R: **arquivar, mover para Lixeira, apagar definitivo** ("marcar como lido" recusado).
- P: Pastas? → R: **só a Caixa de entrada** (INBOX).
- P: Cache? → R: sim, salvar catálogo local — **cuidado com memória excessiva**.
- P: Dependências? → R: poucas (rich, questionary).
- P: Critérios para já sugerir grupos? → R: promoções/newsletters, notificações automáticas, antigos por data, remetentes específicos, anexos grandes.
- P: Agrupar como? → R: por remetente, grupos ≥5 emails ou ≥20MB; resto agregado em "diversos".
- P: Atalhos em massa na revisão? → R: sim, além do refino grupo a grupo.
- P: Ambiente? → R: uv + pyproject.toml.
- P: Visual? → R: UI caprichada no terminal, no espírito das interações do opencode (setas, checkbox de grupos, descrições por opção).
- P: Teto de 500 linhas estourou na Parte 1 (948)? → R: adaptado neste repo pessoal — vale como meta por commit, não por branch; registro na spec.
- P: Usar graphify nas Partes 2–3? → R: sim; gerar o grafo do repo (`graphify extract . --code-only`) e usar `check-update`/`explain` nos subagentes.

## Premissas

Nenhuma — todo ponto ambíguo foi perguntado.

## Design técnico

### Estrutura do repo

    email-cleanup/
      pyproject.toml          # uv; deps: rich, questionary; dev: pytest
      .gitignore              # .env, .data/, .venv/, __pycache__/, .pytest_cache/
      .env.example            # GMAIL_ADDRESS, GMAIL_APP_PASSWORD (placeholders)
      README.md
      docs/specs/2026-10-15-email-cleanup.md
      src/email_cleanup/{__init__,__main__,cli,config,db,imap,catalog,classify,rules,review,apply}.py
      tests/{fake_imap.py,test_config.py,test_db.py,test_imap.py,test_catalog.py,test_classify.py,test_rules.py,test_apply.py}

CLI: `email-cleanup catalog|review|apply` (também `python -m email_cleanup`).
Convenções do workspace Clanada adaptadas: código em inglês, mensagens ao
usuário em PT-BR, type hints, sem comentários, regras como funções puras.

### Schema SQLite (`.data/catalog.sqlite`, WAL)

- `messages(uid PK, sender, sender_name, domain, subject, internal_date, size, seen, has_attachment, labels, fetched_at)`
- `rules(id PK, created_at, action, filter_json, description)` — ação ∈ manter|arquivar|lixeira|apagar
- `manifest(id PK, uid, action, batch_id, status, executed_at)` — auditoria + retomada de apply
- `meta(key PK, value)` — `last_uid`, `account`, `catalog_finished_at`

### Fluxo IMAP (imaplib stdlib; exigir capabilities `X-GM-EXT-1` e `UIDPLUS`)

- Conexão: `IMAP4_SSL("imap.gmail.com", 993)`; senha de app com espaços removidos.
- `catalog`: `EXAMINE INBOX` (somente leitura), `UID SEARCH ALL`; busca
  `(RFC822.SIZE INTERNALDATE FLAGS ENVELOPE BODYSTRUCTURE X-GM-LABELS)` em lotes
  de 500 UIDs; parse → INSERT + commit por lote. Retomada: continua após
  `last_uid`; `--refresh` re-baixa; `--limit N` para teste pequeno. Ctrl+C
  encerra graciosamente (lote atual commitado). `X-GM-LABELS` é best-effort
  (ausência não quebra).
- Ações (lotes de 200 UIDs, com progresso):
  - arquivar = `UID STORE -X-GM-LABELS (\Inbox)`
  - lixeira = `UID MOVE` para a pasta descoberta em `LIST` pelo flag `\Trash`
    (nome localizado, ex.: `[Gmail]/Lixeira` — nunca chutar o nome)
  - apagar definitivo = `UID MOVE` para a Lixeira, localizar de novo lá via
    `UID SEARCH X-GM-MSGID` (o UID muda de caixa para caixa no IMAP) e então
    `UID STORE +FLAGS (\Deleted)` + `UID EXPUNGE` lá dentro (irreversível)
- `apply` pula UIDs já com status ok no manifest para a mesma ação (idempotente
  após interrupção); falha de lote registra o lote e para.

### Heurísticas (funções puras, listas PT+EN fáceis de calibrar)

- `fold()`: minúsculas + remoção de acentos para casar "promoção"/"promocao".
- `is_promotion`: subject/remetente casando termos (desconto, oferta, promoção,
  imperdível, frete grátis, sale, deal, newsletter, cupom, liquidação…) ou
  local-part tipo promo/news/ofertas/deals/shop/loja.
- `is_notification`: local-part (no-reply, noreply, nao-responda, notification,
  alert, aviso, mailer-daemon…) ou subject (fatura, seu pedido, código de
  verificação, boleto, senha…).
- Flags independentes (não exclusivas); ambos computados no `catalog`.
- `has_attachment`: heurística — token `ATTACHMENT` no BODYSTRUCTURE bruto.
- Idade por `INTERNALDATE`; domínio = parte após `@` do remetente normalizado.
- Grupos = remetente com contagem ≥5 ou tamanho ≥20MB; o resto soma no
  pseudo-grupo "diversos". Ordenação por tamanho desc.

### Regras e resolução do plano

- Toda decisão da revisão vira uma **regra** persistida (descrição legível +
  filtro serializado + ação). "Manter" é ação explícita (override).
- Resolução do plano por UID: **a última regra que casa vence**; o resumo
  mostra conflitos (UIDs atingidos por >1 regra) e totais por ação.
- Atalhos em massa = regras com filtro por categoria/idade (>N anos)/tamanho
  (>X MB)/remetente; refino por grupo = regra por remetente, criada depois
  (portanto vence).

### UI da revisão e aplicação (rich + questionary)

- Painel de abertura (rich): conta, total catalogado, tamanho total, nº de
  grupos, promoções/notificações, email mais antigo, data da última catalogação.
- Menu principal (questionary `select`, setas + enter):
  1. Decidir grupos da página — checkbox multi-seleção (↑↓ navega, espaço
     marca, enter confirma), cada linha com descrição
     "1.234 emails • 2,1 GB • 80% promoções"; mudar página / filtrar por
     remetente ou domínio antes de selecionar.
  2. Atalho em massa — categoria (promoções/notificações), idade (>N anos),
     tamanho (>X MB) ou remetente/domínio (texto).
  3. Ver regras — id, descrição, ação, nº de UIDs atingidos.
  4. Remover regra — escolher da lista.
  5. Prévia do plano — contagens por ação + conflitos, sem sair da revisão.
  6. Concluir.
- Tabela de grupos (rich): nº, remetente, emails, tamanho, idade do mais
  antigo, ação atual (— / manter / arquivar / lixeira / apagar) com cores
  discretas; 25 por página; ordenada por tamanho desc; ✔ no que já foi decidido.
- Ao escolher ação para a seleção: `select` com descrições
  ("Arquivar — sai da caixa, continua na conta", "Apagar definitivo —
  irreversível, esvazia a Lixeira") e resumo do impacto (nº de mensagens e
  tamanho) antes de gravar a regra.
- Apagar definitivo: confirmação digitada ao criar a regra + a confirmação
  final do `apply`.
- `catalog` com barra de progresso (rich); `apply` com painel de prévia,
  barra por lote e resumo final.
- Estilo: `questionary.Style` custom (mesmo espírito do terminal do
  opencode), mensagens PT-BR, símbolos discretos (✔ ⚠ ➜).
- Sem TTY → erro claro orientando usar um terminal interativo.

### Segurança e confirmações

- `catalog`/`review` não mutam nada (EXAMINE; regras só no SQLite local).
- `apply` sempre imprime prévia (contagens por ação + amostra de até 10
  remetentes) e exige digitar `APLICAR`; com apagar definitivo no plano, a
  confirmação é `APAGAR DEFINITIVO`. `--dry-run` para na prévia.
- `apply` sem regras → mensagem orientando rodar `review`.
- Credenciais só via env; nunca impressas/lidas de arquivo pelo script além
  do suporte do usuário (`export`/direnv); `.env` no `.gitignore`.

### Erros e bordas

- Env ausente ou login rejeitado → mensagem clara (sem senha) e exit ≠ 0.
- Queda de rede → retomar (catálogo e apply); assunto com charset quebrado →
  decode com fallback `errors="replace"`.
- `review` exige TTY (erro claro se não for).

### Constantes (ajustáveis em `config.py`)

`FETCH_BATCH=500`, `ACTION_BATCH=200`, `GROUP_MIN_COUNT=5`, `GROUP_MIN_SIZE=20MB`.

## Arquivos que a mudança toca

Todos **criar** (repo novo, lista completa):

- `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`
- `docs/specs/2026-10-15-email-cleanup.md`
- `src/email_cleanup/__init__.py`, `__main__.py`, `cli.py`, `config.py`,
  `db.py`, `imap.py`, `catalog.py`, `classify.py`, `rules.py`, `review.py`, `apply.py`
- `tests/fake_imap.py`, `test_config.py`, `test_db.py`, `test_imap.py`,
  `test_catalog.py`, `test_classify.py`, `test_rules.py`, `test_apply.py`

## Triagem de implementação

| Métrica | Valor |
| --- | --- |
| Tarefas | 13 |
| Arquivos que a mudança toca | 24 |
| Linhas de lógica estimadas | ≈1.100 no total → **3 partes** (≈430 / ≈500 / ≈200) |
| Toca em tela (E2E) | Não (TUI de terminal) |
| Migração de dados | Não |
| Muda contrato de API | Não |
| Dependência nova | Sim (rich, questionary; pytest dev) |
| Repos envolvidos | 1 (novo: `~/Projects/email-cleanup`) |

**Modo sugerido:** agent-driven — 13 tarefas, 24 arquivos, >500 linhas; dividir
em 3 partes dentro do orçamento de 500 cada (em vez de um PR só).

## Tarefas

**Parte 1 — Fundação e catálogo**
- [x] 1. Setup do repo: uv/pyproject, deps, `.gitignore`, `.env.example`, README esqueleto; commit inicial com esta spec.
- [x] 2. Testes (falhando) de `config.py`/`db.py` + `tests/fake_imap.py` (dublê de IMAP4_SSL).
- [x] 3. `config.py` + `db.py` (schema, WAL, helpers).
- [x] 4. Testes (falhando) de `imap.py` (capabilities, descoberta da Lixeira) e `catalog.py` (lotes, retomada, `--limit`, labels ausentes, subject codificado).
- [x] 5. `imap.py` + `catalog.py` + comando `catalog` (progresso rich, Ctrl+C gracioso).

**Parte 2 — Análise e revisão**
- [x] 6. Testes (falhando) de `classify.py`.
- [x] 7. `classify.py` (fold de acentos, flags, anexo, idade, grupos com corte → "diversos").
- [x] 8. Testes (falhando) de `rules.py`.
- [x] 9. `rules.py` (persistência + resolução última-regra-vence + conflitos + skip de manifest).
- [x] 10. `review.py` (UI: painel-resumo, tabela paginada colorida, menu, checkbox de grupos com descrições, atalhos em massa, ver/remover regras, prévia do plano) + comando `review`.

**Parte 3 — Aplicação**
- [x] 11. Testes (falhando) de `apply.py`.
- [x] 12. `apply.py` + comando `apply` (painel de prévia, `--dry-run`, confirmações digitadas, lotes com barra de progresso, manifest).
- [x] 13. README final: pré-requisitos (2FA + senha de app + IMAP habilitado), fluxo de uso, solução de problemas.

## Critérios de aceite

- [ ] Dado env válido e IMAP habilitado, quando rodo `catalog --limit 200`, então o SQLite tem ≤200 mensagens da INBOX com remetente/data/tamanho/flags e nenhuma escrita é feita na conta.
- [ ] Dado catálogo interrompido no meio, quando rodo `catalog` de novo, então retoma do último UID sem re-baixar anteriores (memória estável: lotes + commit por lote).
- [ ] Dado catálogo completo, quando rodo `review`, então vejo grupos por remetente (≥5 ou ≥20MB) ordenados por tamanho com "diversos" agregando o resto, e consigo criar regras em massa e por grupo.
- [ ] Dadas regras sobrepostas, quando resolvo o plano, então cada UID recebe a ação da última regra que o atinge e conflitos são reportados.
- [ ] Dado um plano, quando rodo `apply --dry-run`, então vejo contagens + amostra por ação e nenhum comando de escrita é enviado.
- [ ] Dado arquivar/lixeira confirmados com `APLICAR`, então: arquivar = `-X-GM-LABELS (\Inbox)`; lixeira = `MOVE` para a pasta `\Trash` descoberta; cada UID no manifest.
- [ ] Dado apagar definitivo + confirmação `APAGAR DEFINITIVO`, então mensagens vão à Lixeira e são expurgadas de lá, com manifest.
- [ ] Dado confirmação não digitada (ou Ctrl+C na prompt), então nada é executado.
- [ ] Dado env ausente/login rejeitado, então mensagem clara sem senha e exit ≠ 0.
- [ ] Dado apply interrompido por erro, quando rodo `apply` de novo, então UIDs ok no manifest são pulados e o restante segue.
- [ ] Nenhuma saída do script imprime senha; `.env` fora do git.

## Telas e fluxos E2E (Playwright)

Não se aplica — é um CLI de terminal (TUI rich/questionary).

## Testes

- Automatizados: `uv run pytest` — fake IMAP em memória (sem rede); testes de
  heurísticas, agrupamento, regras/resolução, catálogo (lotes/retomada/labels),
  apply (comandos emitidos por ação, batching, manifest, falhas, confirmações).
- Manual (usuário): gerar senha de app, `catalog --limit 200` → `catalog`
  completo → `review` → `apply --dry-run` → aplicar um grupo pequeno de
  arquivar/lixeira e conferir no Gmail antes de soltar o resto.

## Verificação independente

Subagente em contexto limpo (2026-10-15) revisou a branch contra a spec: 129 testes verdes, todos os critérios de aceite atendidos em código/testes, com 10 achados.

- Bloqueante: apagar definitivo não idempotente (podia gravar `ok` sem expurgar).
- Importantes: `ok` falso para UID inexistente; erro por lote impreciso; `.env` não carregado; metas `account`/`catalog_finished_at` não gravadas; credenciais validadas só após a confirmação.
- Menores: `apply` sem TTY; sem UIDVALIDITY; `--limit` com `last_uid`; falta teste de regressão.

Todos corrigidos em 4 commits (78fc5cd, 11facfa, b8386e8, 174be82), 152 testes verdes. Limite conhecido: no apagar definitivo uma queda no meio marca o lote (≤200) como `erro`; é reaplicável e idempotente. Linhas catalogadas antes da coluna `gm_msgid` que já saíram da INBOX viram `ausente`. A reverificação das correções foi a suíte de testes, não um segundo subagente.

## Registro de testes

- Automatizados: `uv run pytest -q` → 152 passed (fake IMAP em memória, sem rede).
- Manual / QA no Gmail real: **pendente, a cargo do usuário** — `catalog --limit 200` → `catalog` → `review` → `apply --dry-run` → aplicar um grupo pequeno de arquivar/lixeira e conferir; testar apagar definitivo com 1–2 emails antes de regras grandes.
- Push / MR: repo sem remote e sem branch `main`; nada enviado.

## Riscos e pontos em aberto

- Heurísticas de categoria são um chute inicial; calibrar com o catálogo real.
- Experimento de EXPUNGE na Lixeira do Gmail é irreversível: validar primeiro
  com 1–2 emails de teste antes de qualquer regra real de apagar.
- 11k via IMAP leva tempo (bem tolerável em lotes); o catálogo é rodado uma vez.
- Se `X-GM-LABELS` não vier como esperado, catálogo segue com heurísticas.
- Repo novo sem remote: push/MR do `/pato/close-spec` só quando houver remote.
- Sensibilidade a throttling do Gmail em rajadas: lotes + retomada cobrem.
- Teto de 500 linhas adaptado por decisão do usuário: neste repo pessoal vale como meta por commit, não por branch (a Parte 1 fechou ≈950).
- Se a Parte 2 estourar os 500, entregar em dois commits lógicos (regras por
  grupo → atalhos em massa + polimento da UI).
