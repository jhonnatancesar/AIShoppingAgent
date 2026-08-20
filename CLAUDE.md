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

A V1 permitirá pesquisar Pichau, Terabyte, Amazon e Kabum, conforme seleção do usuário. Mercado Livre, Shopee e AliExpress serão apresentados pelo bot como ***Futuro***, sem seleção ou coleta. Cada fonte ativa exige Store Provider próprio; nenhuma fonte será descoberta ou integrada automaticamente.

## Guardrails

- Todo o trabalho neste projeto — incluindo mensagens de progresso, raciocínio apresentado, hipóteses, achados, avisos, relatórios, conclusões e toda a documentação (TASKs, ROADMAP, CHANGELOG, PROJECT_CONTEXT) — deve ser conduzido em português do Brasil (PT-BR), sem exceção, mesmo quando ferramentas/comandos/bibliotecas retornarem saída em inglês. Só ficam literais em inglês os elementos técnicos que precisam permanecer assim (código-fonte, nomes de função/classe/arquivo, caminhos, comandos de terminal, parâmetros, variáveis de ambiente, identificadores, stack traces, mensagens de erro literais, SQL, payloads) — nunca usar isso como desculpa para escrever a explicação inteira em inglês. Mensagens de commit podem seguir Conventional Commits, com a descrição em português.
- Não implementar nada além da TASK solicitada.
- Antes de iniciar uma TASK, identificar todos os recursos necessários ao desenvolvimento e à validação real. Solicitar antecipadamente ao usuário qualquer chave, conta, permissão ou configuração ausente, orientando seu armazenamento seguro fora do Git e do chat.
- Não conectar módulos diretamente a Gemini, OpenAI ou Claude: o AI Provider Manager será a única porta de acesso.
- USER e DEV usam exclusivamente a cascata gratuita Gemini → Groq
  `openai/gpt-oss-120b` → OpenRouter `openrouter/free`. Grounding DEV usa a
  Firecrawl API v2 direta antes da mesma cascata; Firecrawl é pesquisa, não
  LLM. ADMIN compartilha a política gratuita vigente. PLUS é futuro.
- Cada coleta de preço deverá ser persistida quando o mecanismo for implementado.

## Fonte de verdade

`docs/PROJECT_CONTEXT.md` registra o estado vivo; `docs/ROADMAP.md` registra a ordem de trabalho; `docs/tasks/` contém o escopo unitário.

## Continuidade no servidor — estado atual em 2026-08-20

O ambiente autoritativo é `C:\App\AIShoppingAgent`, no Windows Server. A
`main` está em `df7609b446612ff106c150a72902c99306c3ab58` (tag
`v1.0.10`), igual a `origin/main`. `v1.0.8` corrigiu o formato do link do
Telegram ("Ver anúncio") com `parse_mode="HTML"`; `v1.0.9` corrigiu a
causa raiz real (`AISHOPPING_AUTH_PUBLIC_BASE_URL` faltando no
`telegram_notifier`). `v1.0.10` (`DEC-070`): diagnóstico dedicado (sem
evasão) confirmou bloqueio persistente de Cloudflare Bot Management na
Terabyte (`server: cloudflare`, `cf-mitigated: challenge`, cookie
`__cf_bm`, 403 em homepage/busca/produto; Chrome comum no mesmo IP do
servidor carrega normal, Playwright do coletor não). Duas ações: (1)
**Terabyte desativada** (`stores.is_active=false`, ação de dados
reversível, aplicada antes do deploy de código); (2)
`TerabyteProvider.resolve_installment_options` removido -- quando
reativada, a Terabyte não navega mais para página individual só por
parcelamento, usa só o card (como Amazon/KaBuM!). Confirmado no container
recém-implantado que o método já não existe e que `is_active` continua
`false` depois do deploy (deploy de código nunca mexe em dado). Pichau,
Amazon, KaBuM! inalterados. O head do
Alembic continua
`20260817_0001` (migration de `offer_installment_options`, aplicada com
`alembic upgrade head` a partir de `20260811_0001`, passando por
`20260816_0001`/`20260816_0002`). Backup operacional validado antes da
migration (`backups/postgres-20260817T231541Z-pre-v1.0.7.dump`,
restauração testada em banco de validação separado). TASK-077, TASK-084,
TASK-088 e TASK-089 (com `DEC-069`) estão concluídas e publicadas;
nenhuma TASK está pendente no momento. Apresentação Telegram já
mostra `💰 À vista`/`💳 Parcelado` dinamicamente; interpretação de pedido
de compra parcelada pelo usuário ("quero em 6x") foi explicitamente
adiada para uma V2, sem código residual desta rodada. O WSL2 opera com
limite de 4 GB, swap de 2 GB e reclaim gradual. Somente schedules de
missões `active` podem ficar habilitados; `cancelled`, `completed` e
`expired` ficam desabilitados.
