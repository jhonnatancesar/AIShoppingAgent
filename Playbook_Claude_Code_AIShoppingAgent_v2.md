# AIShoppingAgent - Playbook Claude Code v2

## Objetivo

Construir um agente inteligente de compras de forma incremental.

## Regras Globais

-   Antes de qualquer tarefa, leia `CLAUDE.md` e toda a pasta `docs/`.
-   Implemente apenas a TASK solicitada.
-   Atualize `PROJECT_CONTEXT.md` e `CHANGELOG.md`.
-   Nunca implemente funcionalidades futuras.

## Arquitetura alvo (MVP)

-   FastAPI
-   PostgreSQL
-   Docker Compose
-   Playwright
-   Telegram
-   Monólito modular
-   Sistema de Missões
-   Histórico completo de preços

## Estrutura inicial

Crie: docs/ adr/ rfc/ tasks/ future_ideas/ backend/ frontend/ tests/
docker/ scripts/

Arquivos: README.md CLAUDE.md docs/PROJECT_CONTEXT.md docs/ROADMAP.md
docs/CHANGELOG.md docs/VISION.md docs/ARCHITECTURE.md docs/DATABASE.md
docs/MISSION_SYSTEM.md docs/AGENTS.md docs/PRICE_ENGINE.md
docs/PURCHASE_ENGINE.md docs/TELEGRAM.md docs/EVENT_MONITOR.md
docs/AI_PROVIDER_MANAGER.md

## RFCs

Criar RFCs para: - Missões - Banco - IA - Telegram - Compra - Eventos -
AI Provider Manager

## ADRs

Criar ADRs para: - Monólito modular - PostgreSQL - Docker Compose -
Missões permanentes - Histórico de preços - AI Provider Manager

## AI Provider Manager (documentar, NÃO implementar)

-   Toda IA passa por um gerenciador único.
-   Nenhum módulo acessa Gemini/OpenAI/Claude diretamente.
-   Perfis:
    -   USER -\> Gemini.
    -   ADMIN -\> melhor IA disponível com fallback.
    -   DEV -\> melhor IA disponível com fallback.
    -   PLUS (futuro) -\> múltiplas IAs.
-   Se USER atingir limite gratuito: informar para tentar novamente mais
    tarde.
-   O sistema deve estar preparado para expansão, mas o plano PLUS NÃO
    deve ser implementado agora.

## Histórico de preços

Toda coleta deve ser armazenada. Nunca descartar histórico sem política
explícita.

## TASK-000

Criar toda a estrutura, documentação, RFCs, ADRs e gerar entre 50 e 70
arquivos em docs/tasks/TASK-XXX.md.

## TASK padrão

Leia CLAUDE.md, docs/, ROADMAP e apenas a TASK indicada. Implemente
somente essa tarefa. Atualize documentação e testes.
