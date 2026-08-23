# TASK-105 — Reativação da Terabyte via Edge/CDP

Status: **Concluída no DEV; validação mínima focada, pronta para commit.**

## Objetivo

Reativar a Terabyte como fonte pesquisável trocando somente o transporte de
coleta (Playwright gerenciado → Edge/CDP), sem alterar parser, ranking,
normalização ou qualquer consumidor já existente.

## Contexto / motivação

`DEC-070` (2026-08-20) documentou bloqueio persistente do Cloudflare Bot
Management contra o Chromium gerenciado pelo Playwright (403,
`cf-mitigated: challenge` em homepage/busca/produto) e desativou a Terabyte
(`stores.is_active=false`) como ação de dados reversível, mantendo o código
do provider intacto.

Diagnóstico repetido no DEV em 2026-08-22 confirmou que o mesmo bloqueio não
se repete quando a navegação passa por um Edge normal via CDP loopback (mesmo
padrão já validado e em produção para a Magalu):

- busca real (`RTX 5070`) → HTTP 200, sem qualquer sinal de challenge
  Cloudflare, 300 cards visíveis na página;
- `TerabyteProvider.extract()` atual, sem nenhuma alteração de código, extraiu
  20 ofertas reais (título, preço, URL) dentro do limite já configurado;
- página individual do primeiro resultado → HTTP 200, sem bloqueio;
- `resolve_product_availability` retornou evidência real ("Disponível").

Ou seja: a causa raiz documentada em `DEC-070` continua correta como
diagnóstico histórico, mas foi superada por um transporte diferente — não por
contorno de proteção anti-bot. É o mesmo tipo de navegador real (Edge normal,
sem stealth/spoofing/proxy) já usado hoje pela Magalu e pelo fallback do
Mercado Livre.

## Escopo

- trocar o transporte operacional de busca (e, se necessário, do
  enriquecimento de página individual) da Terabyte de Playwright gerenciado
  para Edge/CDP;
- reutilizar a infraestrutura CDP já existente (supervisor de Edge dedicado,
  endpoint loopback, validação de endpoint) sem criar um segundo Edge
  supervisionado só para a Terabyte;
- parser (`TerabyteProvider.extract()`), seletor de resultado, ranking,
  normalização de preço, identidade global e todos os consumidores (Web,
  Telegram, missões) permanecem exatamente como estão;
- reativação de `stores.is_active` acontece por migration Alembic reversível
  (mesmo mecanismo já usado pelo seed original) — a Terabyte sobe ativa no
  próximo deploy sem exigir `UPDATE` manual em produção, mas continua sendo
  uma mudança deliberada e rastreável, nunca um valor implícito de código.

## Regras de transporte

- reutilizar a mesma abstração/endpoint CDP já usado pela Magalu (e pelo
  fallback do Mercado Livre) sempre que fizer sentido — não duplicar
  supervisor, perfil de Edge dedicado ou porta;
- o Edge continua supervisionado automaticamente (início/recuperação), do
  mesmo jeito que já acontece hoje; a Terabyte não ganha um ciclo de vida de
  navegador próprio;
- a porta CDP aceita somente loopback (`127.0.0.1`/`localhost`/`::1`) com
  porta explícita — mesma validação já usada
  (`validate_loopback_cdp_endpoint`);
- timeout e falha da Terabyte são isolados: um bloqueio/erro do Edge nessa
  fonte não derruba as demais lojas nem interrompe a orquestração;
- **sem fallback para o Playwright gerenciado** nesta fonte — ele já está
  comprovadamente bloqueado (`DEC-070`); se o Edge/CDP falhar, a claim da
  Terabyte falha isolada, sem tentar reabrir o transporte antigo;
- `DEC-070` permanece registrada como está, como histórico do diagnóstico
  original; este TASK só acrescenta que a causa foi superada por um
  transporte diferente, sem invalidar o diagnóstico.

## Implementação

- `TerabyteProvider` ganhou `cdp_transport: CdpPageFallback | None`
  (construtor). `_collect_once` foi sobrescrito para navegar e extrair via
  `self._cdp_transport.run(...)` em vez de `BrowserSession` — segue
  exatamente o padrão "transporte único, sem fallback" da Magalu (não o
  padrão "fallback após Playwright" do Mercado Livre), como a regra de
  "sem fallback Playwright" já apontava. Sem `cdp_transport` configurado,
  a claim falha isolada (`CdpFallbackError`), nunca cai de volta ao
  Playwright;
- `extract()`, `result_selector`, `resolve_product_availability` e a
  remoção de `resolve_installment_options` (`DEC-070`) continuam
  exatamente como estavam — nenhum parser/regra comercial mudou;
- reaproveitada a classe `CdpPageFallback` (`app/collection/providers/
  cdp_fallback.py`) tal como já existia para o Mercado Livre — sem classe
  nova, sem segundo supervisor Edge, sem porta própria;
- `build_collection_adapter` (`app/collection/worker.py`) passa a injetar
  `cdp_transport=CdpPageFallback(settings.edge_cdp_url, ...)` na Terabyte
  quando `edge_cdp_url` está configurado — mesma variável compartilhada por
  Magalu e Mercado Livre;
- `Settings.magalu_cdp_url` foi renomeado para `Settings.edge_cdp_url`
  (`app/core/config.py`) — o nome antigo era específico da Magalu e a
  mesma infraestrutura agora atende três lojas. Compatibilidade
  preservada via `AliasChoices`: `AISHOPPING_MAGALU_CDP_URL` (env/`.env`)
  continua funcionando, sem exigir reconfiguração de ambiente já
  implantado; `AISHOPPING_EDGE_CDP_URL` é o nome atual e tem precedência
  se os dois estiverem definidos. `scripts/validate_store_providers.py`
  (`--edge-cdp-url`) e a documentação (`docs/architecture/playwright.md`,
  `docs/development/dependencies.md`, `.env.example`) foram atualizados
  junto;
- nova migration Alembic (`20260822_0009_reactivate_terabyte.py`, head
  atual) marca `stores.is_active=true` para `code='terabyte'` -- reverte
  exatamente a ação de dados manual do `DEC-070`, mas por um mecanismo
  automatizado e versionado (roda em qualquer `alembic upgrade head`,
  sem `UPDATE` manual em produção); `downgrade()` retorna a `false`,
  simétrico e reversível;
- `DEC-070` (`docs/internal/decision-log.md`) recebeu um bloco de
  atualização registrando que a desativação temporária terminou com a
  troca de transporte, sem apagar o diagnóstico original.

## Validação mínima

- busca real retorna múltiplas ofertas (não só 1 card);
- `TerabyteProvider.extract()` atual, sem alteração, processa o conteúdo
  obtido pelo novo transporte;
- página individual abre e `resolve_product_availability` retorna evidência
  real;
- falha isolada do Edge/CDP na Terabyte não interrompe nem bloqueia a coleta
  das demais lojas ativas;
- `stores.is_active` volta a `true` por migration, sem `UPDATE` manual.

## Fora de escopo

Novo parser, nova regra comercial de vendedor/condição/parcelamento da
Terabyte, alteração de ranking, autenticação, outras lojas, suíte completa de
testes, Docker, commit/push, deploy.
