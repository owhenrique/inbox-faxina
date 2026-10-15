# email-cleanup

CLI que cataloga a caixa de entrada antiga do Gmail via IMAP e permite revisar e limpar as mensagens em grupos, com prévia e confirmação.

## Pré-requisitos

1. Verificação em duas etapas (2FA) ativada na conta do Google.
2. IMAP habilitado: Gmail → Configurações → Ver todas as configurações → Encaminhamento e POP/IMAP → Ativar IMAP.
3. Senha de app gerada em <https://myaccount.google.com/apppasswords> (espaços são ignorados).
4. [uv](https://docs.astral.sh/uv/) instalado.

## Configuração

```bash
uv sync
cp .env.example .env   # preencha GMAIL_ADDRESS e GMAIL_APP_PASSWORD
```

As credenciais são lidas do `.env` na raiz do projeto; variáveis já definidas no ambiente têm precedência. Nada é impresso e `.env` está no `.gitignore`.

## Uso

```bash
uv run email-cleanup catalog --limit 200   # teste pequeno (somente leitura)
uv run email-cleanup catalog               # catálogo completo, retoma se interrompido
uv run email-cleanup review                # decide por grupo ou em massa (só regras locais)
uv run email-cleanup apply --dry-run       # prévia, nada é enviado
uv run email-cleanup apply                 # executa após digitar APLICAR
```

- `catalog` baixa apenas os UIDs ainda ausentes do banco, então execuções repetidas com `--limit` continuam de onde pararam sem perder nada. `--refresh` re-baixa tudo; Ctrl+C encerra com o lote atual salvo.
- O catálogo guarda o `UIDVALIDITY` da INBOX; se mudar, o `catalog` e o `apply` abortam e pedem para apagar `.data/catalog.sqlite` e catalogar de novo.
- `review` é interativo: ↑↓ navega, espaço marca, enter confirma. Cada decisão vira uma regra; a última regra que casa vence.
- Ações: **manter** (override), **arquivar** (sai da caixa), **lixeira** (Lixeira do Gmail) e **apagar definitivo** (irreversível).
- `apply` mostra prévia com contagens e amostra de remetentes e exige digitar `APLICAR`; com apagar definitivo no plano, `APAGAR DEFINITIVO`.
- UIDs já aplicados com sucesso ficam no manifest e são pulados ao rodar `apply` de novo.

Recomendado: aplicar primeiro um grupo pequeno de arquivar/lixeira e conferir no Gmail; teste apagar definitivo com 1–2 emails antes de qualquer regra grande.

## Testes

```bash
uv run pytest
```

## Solução de problemas

| Sintoma | Causa provável |
| --- | --- |
| `Variável GMAIL_ADDRESS ausente` | `.env` ausente na raiz do projeto ou sem a variável. |
| `Login rejeitado pelo Gmail` | Senha de app inválida ou 2FA desativado. |
| `não anunciou os recursos necessários` | IMAP desabilitado na conta. |
| `Pasta da Lixeira não encontrada` | Rótulo `\Trash` oculto no IMAP: reative em Configurações → Rótulos. |
| `A revisão precisa de um terminal interativo` | `review` rodado sem TTY (pipe/CI). |
| Lote falhou | Throttling ou queda de rede: rode `apply` de novo, retoma do que faltou. |

## Spec

Escopo, decisões e plano de tarefas em [`docs/specs/2026-10-15-email-cleanup.md`](docs/specs/2026-10-15-email-cleanup.md).
