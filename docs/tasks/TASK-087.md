# TASK-087 — Padronizar textos visíveis ao usuário

Status: **Concluída e validada (2026-08-16).**

## Classificação

**Nova TASK do MVP.** Revisão transversal de UX/copy sobre fluxos já existentes,
sem funcionalidade ou regra de negócio nova.

## Objetivo

Aplicar o catálogo revisado pelo usuário aos textos do Telegram e da página de
autenticação, com PT-BR natural, leitura móvel, quebras de linha consistentes,
listas no padrão `1 — Opção` e confirmações com opções em linhas próprias.

## Escopo

- Primeiro contato, sessão, `/ajuda`, `/missao` e fallback de texto.
- Cadastro e respectivos erros de entrada.
- Autenticação, página web, notificações de sessão e upgrade.
- Criação, listagem, transição, cancelamento e edição de missão.
- Preferências, rate limit, alertas, pré-listas e privacidade.
- Atualização somente dos testes/snapshots afetados pelos textos.
- Sincronização de `docs/TELEGRAM_TEXTS_REVIEW.md` com o catálogo revisado.

## Regras imutáveis

Não alterar comandos, aliases, parsers, valores numéricos aceitos, vocabulário
sim/não, estados, TTLs, ownership, senha, autenticação, missões, cancelamento,
edição, IA, coleta ou alertas. Campos opcionais ausentes não devem produzir
placeholder nem linha vazia.

## Fontes autoritativas

- `backend/app/telegram/router.py`
- `backend/app/telegram/confirmation.py`
- `backend/app/telegram/preferences.py`
- `backend/app/telegram/notifications.py`
- `backend/app/users/registration.py`
- `backend/app/authentication/router.py`
- `backend/app/authentication/passwords.py`
- `backend/app/authentication/service.py`
- `backend/app/privacy/notice.py`

## Critérios de aceite

1. Confirmações exibem opções em linhas próprias.
2. Listas visíveis usam consistentemente `1 — Opção`.
3. Textos de sessão, cadastro e edição são legíveis no celular.
4. Alertas permanecem curtos e preservam seus dados.
5. Campos opcionais não deixam placeholders ou linhas vazias.
6. Nenhuma regra de negócio ou parser muda.
7. Testes focados de texto/Telegram e suíte aplicável aprovados.
8. Ruff e `git diff --check` aprovados.
9. Nenhuma chamada externa, rebuild ou deploy é necessária.

## Especificação aprovada

O texto integral entregue pelo usuário em 2026-08-16, intitulado **“Catálogo
revisado de textos visíveis ao usuário”**, é a especificação de copy. Antes da
implementação, ele deve substituir o catálogo preliminar local em
`docs/TELEGRAM_TEXTS_REVIEW.md`, preservando literalmente o conteúdo aprovado.

## Resultado

- Catálogo aplicado aos fluxos de Telegram, autenticação web, notificações,
  cadastro, preferências e privacidade.
- Listas visíveis padronizadas com travessão e confirmações em linhas próprias.
- Nenhum comando, alias, parser, token aceito, estado, TTL, autorização ou regra
  funcional foi alterado.
- Suíte focada: 294 testes aprovados.
- Suíte não-integração: 1.186 testes aprovados, 1 ignorado e cobertura 90,68%.
- Ruff, `git diff --check` e `docker compose config --quiet` aprovados.
- Nenhuma chamada externa, mudança de banco, rebuild ou deploy.
