# Contexto permanente do projeto

## Integração GG Oferta ↔ César Core

Antes de alterar AI, Search, providers, fallback, grounding, César Core,
OmniRoute, Firecrawl ou Docker/configuração GG ↔ Core, leia integralmente
`C:\cesar-core\docs\architecture\gg-oferta-core.md`. O fluxo oficial é
`GG Oferta → César Core → OmniRoute → providers`; Search e Firecrawl
Scrape são capacidades diferentes. GG Oferta e seu worker nativo nunca devem
depender diretamente de um provider externo (Gemini, Groq, OpenRouter,
Firecrawl, SearXNG ou futuros) para capacidades cobertas por essa arquitetura
— a dependência é sempre o César Core; providers concretos ficam abaixo dele.

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

A V1 permite pesquisar Pichau, Terabyte, Amazon, Kabum, Magalu e Mercado Livre, conforme seleção do usuário (TASK-104A/TASK-104B, publicadas em `origin/main`). Shopee e AliExpress serão apresentados pelo bot como ***Futuro***, sem seleção ou coleta. Cada fonte ativa exige Store Provider próprio; nenhuma fonte será descoberta ou integrada automaticamente.

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
- `context-compress` (MCP): funcional nesta máquina Windows, com uma ressalva importante. O runtime `shell` do `execute`/`batch_execute` corrompe caminhos Windows (perde as barras de `C:\Users\...`) e sempre falha — não usar o runtime `shell`. Usar `execute` com `language=javascript`, rodando o comando via `child_process.execSync('<comando>', { cwd: '<caminho absoluto do projeto>', encoding: 'utf8' })`, sempre definindo o `cwd` correto (confirmado funcionando em 2026-08-31, ex.: `git status --short`). `batch_execute` ainda não foi validado com esse padrão JS — não usar até validar separadamente. Usar `context-compress execute` (JS) automaticamente, sem esperar o usuário pedir, para comandos que possam gerar saída grande — `pytest`, `ruff`, `git diff`/`git log`, `docker logs`, builds, consultas SQL extensas. Comandos pequenos podem seguir por execução normal (Bash/PowerShell). Só cair para Bash direto se o `execute`/JS realmente falhar para aquele comando específico.

## Fonte de verdade

`docs/internal/project-context.md` registra o estado vivo; `docs/internal/roadmap.md` registra a ordem de trabalho; `docs/tasks/` contém o escopo unitário.

## Continuidade no servidor — estado atual em 2026-08-28

**Nota de proveniência:** este bloco foi reconstruído a partir do
histórico Git (`origin/main`, tags, `docs/internal/decision-log.md`)
numa sessão de sincronização de documentação, sem acesso SSH/RDP direto
ao Windows Server nesta rodada. Os fatos abaixo são verificáveis por
`git log`/`git tag`; as descrições de comportamento ao vivo em PROD
citam o `DEC-` correspondente como fonte, não uma verificação própria
desta sessão.

A V1.2 foi implantada em produção em 2026-08-28 numa cascata de dez
tags no mesmo dia, `v1.2.0` a `v1.2.9`, todas descendentes lineares
umas das outras (sem divergência de histórico). `origin/main` está em
`517a5fe` (`v1.2.9`); o head do Alembic é `20260828_0001` (migration
`rescope_store_activity_state`). Resumo da cascata, do mais antigo ao
mais recente: `v1.2.0`/`v1.2.1`/`v1.2.2` fecham lacunas operacionais do
worker nativo Windows encontradas no próprio deploy — `DEC-103`
(`host.docker.internal` não resolvia dentro dos containers; corrigido
com `extra_hosts: host-gateway` só no `ops_controller`, sem mexer no
DNS global) e `DEC-104` (worker crashava no primeiro start real por
faltar secrets obrigatórios; padronizado por
`scripts\manage_collection_worker_config.ps1`, e ACL de `.secrets\`
corrigida para excluir `BUILTIN\Users` herdado). `v1.2.3` a `v1.2.9` são
achados reais de PROD depois do deploy, sem TASK formal registrada em
`docs/tasks/` (mesma lacuna já aceita para a TASK-093, `DEC-076`), mas
citados como TASK-114/115/116 nas mensagens de commit — **não
confundir com as TASK-117/TASK-118 atuais**, que tratam de assunto
totalmente diferente (verificação de e-mail via Cloudflare Access e
integração OmniRoute) e foram renumeradas justamente para não colidir
com estes números já usados: placas-mãe X870E/B550 sendo classificadas
como `Product category=cpu` (guard determinístico por frase de ligação
em `products/identity.py`, reparo de histórico via
`scripts/repair_cpu_identity_misclassification.py`); título/imagem do
alerta e da oferta na Web passam a usar o título bruto real da
`PriceObservation` em vez do nome do `Product` compartilhado; e
`HIGH_ACTIVITY` deixou de ser detectado pela loja inteira e passou a
ser chaveado por `(store_id, scope_id)`. As últimas quatro tags
(`v1.2.5`/`v1.2.6`/`v1.2.7`/`v1.2.8`/`v1.2.9`) são correções pontuais de
webapp encontradas ao vivo: CSP `img-src` bloqueando imagens reais das
lojas, `TypeError` num caminho de coleta que ainda usava a assinatura
antiga de `_is_high_activity`, grid do card de oferta estourando altura
no mobile, e uma corrida de navegação no atalho "Entrar como admin".
TASK-077, TASK-084, TASK-088, TASK-089, TASK-098, TASK-110, TASK-112
(todas as fases) e TASK-113 estão concluídas e publicadas em
`origin/main` — confirmado por `git merge-base --is-ancestor`, não só
pela documentação, que estava desatualizada nesse ponto até esta
sincronização. Terabyte (`DEC-070`) e o restante do estado de dados
descrito na entrada anterior deste arquivo não foram reauditados nesta
sessão — apenas o histórico de código/deploy foi confirmado.
