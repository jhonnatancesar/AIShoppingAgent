# Contexto permanente do projeto

## Missão

Construir, de forma incremental, um agente de compras que pesquisa produtos, registra preços, executa missões e apoia decisões de compra.

## Arquitetura alvo do MVP

- Monólito modular com FastAPI.
- PostgreSQL como banco de dados.
- Docker Compose para ambiente local.
- Playwright para coleta automatizada quando aplicável.
- Telegram como canal inicial de interação.
- Sistema de missões e histórico completo de preços.

## Escopo comercial da V1

A V1 será focada exclusivamente em lojas nacionais. O suporte a marketplaces, como AliExpress, Shopee e Amazon Marketplace, é uma evolução futura e não faz parte do escopo atual.

## Guardrails

- Não implementar nada além da TASK solicitada.
- Não conectar módulos diretamente a Gemini, OpenAI ou Claude: o AI Provider Manager será a única porta de acesso.
- O perfil USER usará Gemini; ADMIN e DEV usarão a melhor IA disponível com fallback. PLUS é futuro e não será implementado no MVP inicial.
- Cada coleta de preço deverá ser persistida quando o mecanismo for implementado.

## Fonte de verdade

`docs/PROJECT_CONTEXT.md` registra o estado vivo; `docs/ROADMAP.md` registra a ordem de trabalho; `docs/tasks/` contém o escopo unitário.
