# TASK-065 — Remover variáveis de modelo obsoletas (`AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL`)

Status: **Concluída em 2026-08-10**, pipeline oficial aprovado e publicada,
autorizada explicitamente pelo usuário como primeira TASK da `v1.0.2`
(`docs/V1_0_2.md`, item 1).

Dependência: nenhuma. Primeira TASK da `v1.0.2`; não bloqueia nem depende
de TASK-066 a TASK-069.

## Contexto

`DEC-052` registrou dois achados durante a preparação de
`docs/PRODUCTION_SETUP.md` para a `v1.0.1`. O primeiro: `.env.example`
(raiz), `backend/.env.example` e o `.env` local declaram
`AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL`, mas `compose.yaml` não
propaga nenhuma das duas a nenhum serviço — o nome do modelo em produção é
sempre o default hardcoded em `Settings.gemini_model`/`Settings.groq_model`
(`backend/app/core/config.py`), independentemente do que estiver nesses
arquivos. O usuário decidiu adiar a correção para a `v1.0.2`, para não
misturar limpeza de configuração morta com a primeira subida real da
`v1.0.1` em produção. `docs/PRODUCTION_SETUP.md` já registra esse achado
como "correção planejada" (linhas 279-295, antes desta TASK).

## Objetivo

Remover as duas variáveis de configuração sem efeito real da árvore de
arquivos de ambiente/exemplo e da documentação que ainda as descreve como
configuráveis, sem alterar a cascata de IA nem qualquer comportamento de
runtime.

## Escopo estrito (definido pelo usuário)

1. Auditar novamente, na árvore de código atual, se `AISHOPPING_GEMINI_MODEL`
   e `AISHOPPING_GROQ_MODEL` continuam sem efeito real.
2. Remover as duas variáveis dos arquivos de ambiente/exemplo onde
   estiverem obsoletas.
3. Remover referências documentais obsoletas.
4. Não alterar a cascata Gemini Flash → Groq (`DEC-050`).
5. Não alterar `manager.py` nem qualquer lógica de IA.
6. Não alterar `compose.yaml` além do estritamente necessário para
   confirmar que essas variáveis não são usadas (auditoria apenas —
   nenhuma edição prevista, já que a ausência delas em `environment:` é a
   própria prova).
7. Não tocar em produção (servidor real da `v1.0.1`).
8. Não criar tag `v1.0.2`.
9. Não iniciar a TASK-066 automaticamente.

## Fora do escopo desta TASK

- `AISHOPPING_TELEGRAM_NOTIFICATION_POLL_SECONDS`/`_BATCH_SIZE` — mesmo
  achado de "não aparecem em `.env.example`" mencionado incidentalmente em
  `docs/PRODUCTION_SETUP.md`, mas **não fazem parte do item 1 da
  `v1.0.2`** e não são tocadas aqui.
- Qualquer um dos outros 4 itens de `docs/V1_0_2.md` (restart policy,
  editar missão, categorias numeradas, pré-lista sem IA).
- A menção stale a "Gemini premium" em `docs/DEPENDENCIES.md` (linhas
  72/75), resíduo da cascata de 3 camadas anterior à TASK-064 — é uma
  imprecisão de documentação sobre a cascata, não sobre as variáveis desta
  TASK; deixada de fora para não ampliar escopo. Sinalizada ao usuário
  separadamente.

## Auditoria (2026-08-10)

### 1. `compose.yaml` — confirmado, sem propagação em nenhum dos 7 serviços

Busca por `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` no arquivo:
zero ocorrências. Os blocos `environment:` de `api`, `telegram_notifier` e
`collection_worker` (os três que recebem variáveis `AISHOPPING_*`) foram
lidos por completo — nenhum dos dois nomes aparece em nenhum dos três.
`database`, `otel-collector`, `prometheus` e `jaeger` não usam nenhuma
variável `AISHOPPING_*` de IA. Confirma o achado do `DEC-052`: em
Docker/produção, o nome do modelo é sempre o default de
`Settings.gemini_model`/`Settings.groq_model`.

### 2. `Settings` — campos continuam existindo e sendo usados (não tocados)

`backend/app/core/config.py` define `gemini_model` (default
`gemini-3.6-flash`) e `groq_model` (default `openai/gpt-oss-120b`),
ambos lidos por `backend/app/ai_provider/manager.py` para montar a
cascata. Esses dois campos **não foram alterados** — continuam sendo a
única fonte do nome do modelo, só deixam de ser anunciados como
sobrescrevíveis via `.env`/Compose nesta TASK.

### 3. Arquivos tracked com referência às duas variáveis (antes da limpeza)

| Arquivo | Linha(s) | Natureza |
| --- | --- | --- |
| `.env.example` (raiz) | 40 | Exemplo de configuração — remover |
| `backend/.env.example` | 32, 34 | Exemplo de configuração — remover |
| `docs/DEPENDENCIES.md` | 73-74 | Descrição como "configurável" — corrigir |
| `docs/PRODUCTION_SETUP.md` | 279-295 | Achado + "correção planejada" — atualizar para concluído |
| `docs/V1_0_2.md` | item 1 | Escopo da própria TASK — marcar concluído |
| `docs/DECISION_LOG.md` (`DEC-052`) | — | Registro histórico da decisão original — **não editado**, descreve corretamente o estado da época |
| `docs/tasks/TASK-059.md` | 36 | Registro histórico da TASK-059 — **não editado**, descreve corretamente o que foi entregue naquela TASK |

### 4. Arquivos locais não versionados (`.gitignore`)

- `.env` (raiz): não contém `AISHOPPING_GEMINI_MODEL` nem
  `AISHOPPING_GROQ_MODEL` — nada a remover.
- `backend/.env`: contém `AISHOPPING_GEMINI_MODEL=gemini-3.6-flash`
  (mesmo valor do default do código — sem efeito prático mesmo antes desta
  limpeza) e não contém `AISHOPPING_GROQ_MODEL`. Diferente do Compose,
  este arquivo **é** lido diretamente pelo Pydantic Settings quando o
  backend roda fora do Docker (`env_file=BACKEND_DIRECTORY / ".env"`) —
  por isso, ainda que harmless hoje (valor igual ao default), a linha foi
  removida por limpeza de consistência, sem expor conteúdo do arquivo.

## Implementação (2026-08-10)

- **`.env.example`** (raiz): removida a linha `AISHOPPING_GEMINI_MODEL`
  (única ocorrência das duas variáveis neste arquivo).
- **`backend/.env.example`**: removidas as linhas `AISHOPPING_GEMINI_MODEL`
  e `AISHOPPING_GROQ_MODEL`.
- **`backend/.env`** (local, não versionado — `.gitignore`): removida a
  linha `AISHOPPING_GEMINI_MODEL=gemini-3.6-flash` (mesmo valor do default
  do código, sem efeito prático mesmo antes desta limpeza). O `.env`
  (raiz) local não continha nenhuma das duas variáveis — nada a remover.
  Nenhum valor de segredo foi exibido ou alterado neste arquivo.
- **`docs/DEPENDENCIES.md`**: os dois parágrafos que descreviam
  `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` como "modelo padrão
  configurável" foram reescritos para deixar claro que o nome do modelo é
  fixado em `Settings.gemini_model`/`Settings.groq_model`
  (`backend/app/core/config.py`), sem override real via `compose.yaml`.
- **`docs/PRODUCTION_SETUP.md`**: o callout "Achado da auditoria" foi
  dividido — a parte sobre `AISHOPPING_TELEGRAM_NOTIFICATION_POLL_SECONDS`/
  `_BATCH_SIZE` (fora do escopo desta TASK) foi mantida; a parte sobre
  `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` foi trocada de
  "Correção planejada" para "Correção aplicada (TASK-065)", deixando
  explícito que a produção da `v1.0.1` já implantada não foi tocada.
- **`docs/V1_0_2.md`**: item 1 marcado como concluído, com link para este
  arquivo.
- **Nenhum código de runtime alterado**: `backend/app/core/config.py`
  (campos `gemini_model`/`groq_model`), `backend/app/ai_provider/manager.py`
  e `compose.yaml` permanecem exatamente como estavam.

## Validação (2026-08-10)

- **Pipeline oficial completo** (`scripts\check.ps1`, incluindo Docker
  subido manualmente para a etapa de integração): Gitleaks (working tree,
  versioned files, Git history, canary) aprovado; integridade de
  dependências aprovada; lint (`ruff check`) aprovado; formatação
  (`ruff format --check`, 371 arquivos) aprovada; **752 testes unitários/
  de contrato aprovados, 90,63% de cobertura** (limite mínimo 90%);
  migration head confirmada em `20260809_0004`; **14 testes de integração
  PostgreSQL reais aprovados**. Resultado final: `Pipeline local
  aprovado.`
- **Reconfirmação de que nenhuma variável funcional foi removida por
  engano**: `tests/test_config.py` (16 testes) continua aprovado sem
  nenhuma alteração — não testava `AISHOPPING_GEMINI_MODEL`/
  `AISHOPPING_GROQ_MODEL` como variáveis de ambiente lidas pelo Compose
  (só os campos `Settings.gemini_model`/`Settings.groq_model` com seus
  defaults, que não foram tocados). `tests/test_gemini_user_profile.py`
  (22 testes) e `tests/test_groq_provider.py`/`test_ai_provider_contracts.py`
  (cascata Flash→Groq) permanecem 100% aprovados sem edição.
- **Auditoria pós-remoção**: busca por `AISHOPPING_GEMINI_MODEL`/
  `AISHOPPING_GROQ_MODEL` em toda a árvore versionada retorna zero
  ocorrências fora de registros históricos intencionalmente preservados
  (`docs/DECISION_LOG.md` `DEC-052`, `docs/tasks/TASK-059.md`) e desta
  própria TASK.
- **`compose.yaml`**: não editado (confirmado por diff vazio) — a ausência
  das duas variáveis em `environment:` já era a prova de que não tinham
  efeito; nenhuma edição foi "estritamente necessária" além da auditoria.
- **Produção**: nenhum comando executado contra o servidor real da
  `v1.0.1`; nenhuma tag `v1.0.2` criada.

## Encerramento

Concluída em 2026-08-10. `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL`
removidas dos arquivos de exemplo e da documentação que as descrevia como
configuráveis; cascata Gemini Flash → Groq, `manager.py`, `compose.yaml` e
produção da `v1.0.1` intocados. TASK-066 não foi iniciada — aguarda
aprovação explícita do usuário.

## Fora do escopo — reforço final

- Nenhuma tag `v1.0.2` criada.
- Nenhuma alteração em produção.
- TASK-066 não iniciada.
