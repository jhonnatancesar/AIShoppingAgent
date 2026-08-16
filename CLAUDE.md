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

## Continuidade no servidor — estado atual em 2026-08-16

O ambiente autoritativo é `C:\app\AIShoppingAgent`, no Windows Server. A
`main` está em `0e90cf0805a24cfd873d4d0257dacd8ae03c7920`, igual a
`origin/main`, e esse HEAD foi implantado no stack local de 7 serviços. O drift
do `alembic check` foi resolvido pela TASK-086 sem migration; o head permanece
`20260811_0001`. TASK-076 e TASK-087 também estão concluídas. O WSL2 opera com
limite de 4 GB, swap de 2 GB e reclaim gradual. Somente schedules de missões
`active` podem ficar habilitados; `cancelled`, `completed` e `expired` ficam
desabilitados. Próximas TASKs pendentes: TASK-077 e TASK-084, ambas não
iniciadas.
