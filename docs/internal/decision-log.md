# Decision Log

## DEC-096 — TASK-109: migrar collection_worker para Windows nativo com Edge

- **Data:** 2026-08-22.
- **Classificação:** Preflight + plano de migração; nenhum código escrito.
- **Decisão:** tirar o `collection_worker` do Docker/Linux (Playwright/
  Chromium) e rodá-lo como processo Windows nativo controlando um Edge
  real via CDP loopback — generaliza o padrão já validado para Magalu
  (`DEC-090`) e Terabyte (esta sessão, TASK-105) para todas as seis
  lojas. API, PostgreSQL, Telegram notifier, `ops_controller` e
  observabilidade continuam em Docker.
- **Achado favorável do preflight:** a abstração operacional já existe
  pronta para isso -- `app/admin/service_ops.py`
  (`ServiceOps`/`ManagedService`) é runtime-neutro, e
  `app/ops_controller.py` já isola Docker num único
  `DockerOpsAdapter` ("Único ponto que conhece Docker"). A mudança para
  suportar um worker Windows fica local a `ops_controller.py` (novo
  adapter + dispatch por serviço); o painel ADMIN não muda nada.
- **Sem Browser Bridge:** como o worker passa a rodar na mesma máquina
  do Edge, controla via CDP loopback diretamente -- um bridge de rede só
  faria sentido em hosts diferentes.
- **Execução:** Task Scheduler (não Windows Service/Session 0) -- o
  servidor já faz login automático e bloqueia a tela; Session 0 isolaria
  o Edge da sessão interativa que ele pode precisar.
- **Ordem de validação antes de remover Chromium:** Magalu/Terabyte
  (já comprovadas) → Mercado Livre (Edge/CDP como primário, hoje só
  fallback) → Amazon/Kabum/Pichau (nunca testadas em Edge/CDP, mesma
  auditoria real sem evasão já usada nas outras). Chromium só sai depois
  das seis confirmadas.
- **Riscos/pontos em aberto registrados em `docs/tasks/TASK-109.md`:**
  canal de status/controle do `ops_controller` para o worker Windows
  (WSL2 → host) ainda sem solução única óbvia; suposição não confirmada
  de que Postgres/API em `127.0.0.1` do Docker Desktop/WSL2 aparecem no
  `127.0.0.1` do host Windows; comportamento de Edge/CDP com sessão
  Windows bloqueada (não desconectada) não documentado ainda.
- **Fora de escopo:** implementação, deploy, remoção do Chromium antes
  da validação completa das seis lojas.
- **Fechamento (2026-08-23):** as seis lojas confirmadas via Edge/CDP;
  `BrowserSession`/Chromium removidos de todo caminho real de coleta
  (`app/collection/providers/base.py`/`stores.py`) -- sem `cdp_transport`
  configurado, cada provider falha explícito (`EdgeCdpTransportError`),
  nunca mais abre Chromium gerenciado como fallback silencioso.
  `BrowserSession` permanece só como infraestrutura de teste hermética
  (obter um `Page` real para parsing de HTML estático local, sem rede),
  decisão tomada durante o audit ao constatar que ~40 testes a usam
  exatamente para isso, sem relação com o fallback de coleta removido.
  `collection_worker` saiu do `compose.yaml`; Dockerfile parou de instalar
  Chromium/Xvfb (nenhum serviço Docker restante abre navegador); binário
  do Chromium continua necessário só para rodar a suíte de testes
  local/CI (`playwright install chromium`), fora da imagem de produção.
  `ops_controller` ganhou `WindowsOpsAgentAdapter`, resolvendo o "ponto em
  aberto" do canal de status/controle: HTTP loopback assinado
  (HMAC+timestamp+nonce) via `host.docker.internal`, mesmo esquema já
  usado pelo `DockerOpsAdapter`, sem shell genérico. Documentação nova:
  `docs/architecture/windows-collection-worker.md` (arquitetura completa
  do runtime DEV) e `docs/architecture/playwright.md`/
  `docs/architecture/service-operations.md` atualizados. Nenhum deploy
  feito -- produção continua nos sete serviços Docker
  (`docs/installation/windows-server.md` inalterado de propósito).
- **Fechamento, parte 2 (2026-08-24):** usuário pediu zero Chromium
  também na suíte de testes (não só na coleta real). Auditoria dos ~40
  testes que ainda usavam `BrowserSession`: todos eram parsing de HTML
  local (`page.set_content`), nenhum navegava para URL externa --
  categoria "precisa de browser real" ficou vazia. `BrowserSession`
  passou a lançar o Microsoft Edge da máquina
  (`Playwright.chromium.launch(executable_path=...)`) em vez do Chromium
  baixado pelo Playwright; nenhum teste precisou de reescrita (interface
  inalterada). `discover_edge_executable` extraído para
  `app/collection/edge_discovery.py` (fora do pacote `providers`, evita
  import circular com `browser.py`). `python -m playwright install
  chromium` removido de todos os passos de setup (`README.md`,
  `docs/development/dependencies.md`,
  `docs/development/local-pipeline.md`). Resultado: zero binário de
  Chromium em qualquer lugar do projeto, produção e teste na mesma
  direção arquitetural (Edge).

## DEC-095 — TASK-108: fila justa por usuário, cooldown individual, sem monopolização

- **Data:** 2026-08-22.
- **Classificação:** Planejamento/documentação; nenhum código escrito.
- **Contexto real levantado antes de propor desenho:** `run_batch`/
  `claim_due_collections` (`app/collection/orchestration.py`) hoje não têm
  nenhum conceito de usuário — claims de várias missões/usuários entram
  misturadas no mesmo batch e rodam em paralelo sob um único
  `asyncio.Semaphore(max_concurrency)` global. O único isolamento
  existente é por missão (lock de seção crítica) e por `(mission_id,
  store_id)` (backoff, `DEC-046`) — preservado sem alteração.
- **Decisão:** introduzir fila FIFO/round-robin por `user_id`, com
  `max_concurrent_user_batches=1` (só um usuário processado por vez) e
  cooldown individual por usuário (`user_cooldown_min/max_seconds`,
  **1–3 min** com jitter, corrigido pelo usuário -- proposta original de
  10–15 min era longa demais) depois que o lote desse usuário termina;
  esse usuário volta para o **fim da fila**. Cooldown nunca bloqueia
  outros usuários elegíveis, que seguem imediatamente -- confirmado
  explicitamente pelo usuário, não é mais suposição. `coupon_worker`
  (`DEC-093`) permanece inteiramente fora dessa fila. Pesquisa Web
  recebe rate-limit (não fila de processamento própria, confirmado),
  reaproveitando `max_daily_searches` da `DEC-094` -- não consome
  recursos de coleta/provider, então não precisa de fila.
- **Duas camadas de proteção, confirmado pelo usuário:** a proteção
  principal contra excesso de requisições continua sendo por
  provider/loja (circuit breaker por `source_code`, backoff
  `next_eligible_at`/`consecutive_blocks` em `MissionSource`, `DEC-046`)
  -- já existe, intocada. A fila por usuário desta TASK é uma segunda
  camada, ortogonal, contra monopolização do worker por um único
  usuário; um provider em cooldown nunca trava desnecessariamente os
  demais.
- **Fora de escopo:** qualquer alteração nos limites por provider já
  existentes, sistema de planos (`DEC-094`/`DEC-073`).

## DEC-094 — TASK-107: cotas por usuário, sem plano/tier novo

- **Data:** 2026-08-22.
- **Classificação:** Planejamento/documentação; nenhum código escrito.
- **Decisão:** limitar por USER `max_active_missions=5`,
  `max_store_slots=18` (1 loja de missão `ACTIVE` = 1 slot) e
  `max_daily_searches=30`, com revalidação em `resume` e em edição de
  lojas de missão ativa. `PAUSED`/`CANCELLED`/`EXPIRED`/`COMPLETED` nunca
  consomem quota. Nenhuma pausa/cancelamento automático em nenhuma
  circunstância — toda liberação de capacidade é ação explícita do
  usuário. UX sempre visível (uso/limite), aviso antes do limite, e
  explicação com ações contextuais ao bater a quota (nunca só "limite
  excedido"). DEV/ADMIN pode sobrescrever quota por usuário no painel já
  existente (`admin_router.py`), com a mesma auditoria já aplicada a toda
  mutação admin.
- **Compatibilidade com `DEC-073`:** não introduz `user_roles`, múltiplos
  papéis, RBAC avançado nem planos FREE/PLUS/PRO — esses continuam
  reservados para a V2. Os defaults ficam como constantes de sistema
  (`Settings`) com override por usuário como único mecanismo de
  diferenciação hoje; isso deixa o caminho pronto para um futuro sistema
  de planos aplicar valores por tier sem redesenhar o mecanismo de
  verificação/consumo.
- **Base real no schema:** `MissionStatus` (string enum já existente,
  `active`/`paused`/`cancelled`/`expired`/`completed`/`draft`),
  `MissionSource` (`mission_sources`, uma linha por `(mission, store)`,
  contada só para missões `ACTIVE` do usuário — diferente da contagem já
  existente em `_mission_prelist_round_complete`, que não filtra por
  status). `max_daily_searches` não tem infraestrutura hoje; padrão mais
  próximo a adaptar é `reserve_telegram_update`
  (`app/telegram/limits.py`), trocando a janela de 1 minuto por 1 dia.
- **Fora de escopo:** sistema de planos, implementação de código nesta
  rodada.

## DEC-093 — Cupom é subsistema independente, nunca acoplado ao StoreProvider/coleta

- **Data:** 2026-08-22.
- **Classificação:** Correção arquitetural da TASK-106, antes de qualquer
  código (mesmo espírito da `DEC-069`, que corrigiu o desenho do
  parcelamento antes de consolidar).
- **Decisão:** cupom não é um campo a mais coletado durante a busca normal
  de oferta. É um subsistema próprio, com collector, persistência e
  avaliação de aplicabilidade independentes do `CollectionOrchestrator`/
  `collection_worker`. Falha, bloqueio ou lentidão do coletor de cupons
  nunca afeta a coleta de preço; e a coleta de preço nunca espera ou abre
  navegação extra por causa de cupom.
- **Motivação:** a auditoria real (mesmo dia) mostrou que cupom não vive
  no mesmo lugar que a oferta — Amazon/Kabum expõem no card de busca (a
  mesma abertura já usada), mas Magalu/Mercado Livre só mostram na
  **home** da loja, fora do fluxo de busca por produto. Acoplar cupom à
  coleta normal criaria navegação extra por Offer (ex.: abrir a home a
  cada oferta), exatamente o tipo de carga repetitiva que gerou o
  bloqueio observado na própria Shopee horas antes. Separar os dois
  processos evita esse acoplamento estrutural.
- **Arquitetura aprovada (detalhada em `docs/tasks/TASK-106.md`):**
  1. **Coupon Collector** — processo próprio, frequência própria, varre
     fontes oficiais por loja (não por missão/produto); reaproveita
     transporte/provider de cada loja só como infraestrutura (função de
     navegação/parsing), nunca a orquestração do `collection_worker`.
  2. **Persistência** — modelo `Coupon` próprio (não é campo de `Offer`),
     com todos os atributos só quando houver evidência real; nunca
     persistido sem evidência.
  3. **Aplicabilidade** — processo separado cruza cupons ativos com
     ofertas relevantes das missões (`MissionOfferRelevance`), decide
     deterministicamente se um cupom se aplica; regra ambígua nunca vira
     afirmação — fica "possivelmente aplicável".
  4. **Notificação** — só dispara quando o cupom transforma a oferta em
     oportunidade relevante; sempre mostra preço atual, desconto, preço
     estimado, código, regra relevante, link e evidência; deixa explícito
     que o preço é estimado até a aplicação real no checkout.
- **Fora de escopo, mantido de propósito:** nenhuma técnica de evasão;
  Firecrawl e agregadores de terceiro continuam fora desta versão
  (`DEC-088`); nenhum código de provider ou collector foi escrito ainda.

## DEC-092 — Adiar TASK-104C (Shopee) por bloqueio anti-bot mesmo autenticado

- **Data:** 2026-08-22.
- **Classificação:** Bloqueio externo confirmado — reação a proteção
  anti-bot real, não decisão de arquitetura do provider. Mesmo *padrão de
  resposta* usado no `DEC-070` original (diagnosticar sem evasão,
  documentar, adiar), **não** o mesmo resultado: a Terabyte foi resolvida
  trocando o transporte para Edge/CDP (TASK-105); a Shopee já foi testada
  exatamente por esse mesmo caminho — Edge/CDP normal, com e sem login —
  e o bloqueio persistiu (ver "Diagnóstico" abaixo). Não há, hoje, uma
  solução equivalente já validada para a Shopee.
- **O que foi tentado, sem evasão:** busca pública sem login (bloqueada,
  `error: 90309999`, redirecionamento para tela de login); Edge/CDP normal
  (mesmo padrão já validado para Magalu/Terabyte) sem login (mesmo
  bloqueio); login real via Google numa sessão Edge/CDP persistente e
  dedicada (perfil próprio, loopback) — login funcionou e a sessão
  persistiu de fato entre fechar/reabrir o Edge (cookies `SPC_ST`/`SPC_U`
  confirmados), mas tanto a busca quanto uma repetição isolada (sessão
  "fria", uma única requisição) foram redirecionadas para
  `shopee.com.br/verify/captcha?...scene=crawler_item...` (CAPTCHA de
  arrastar peça). A API `search_items` respondeu `error: 90309999` mesmo
  autenticada.
- **Diagnóstico:** diferente da Terabyte (bloqueio por características do
  Chromium gerenciado, resolvido trocando para Edge/CDP -- `DEC-070`/
  TASK-105), a Shopee classifica como `crawler_item` mesmo com Edge normal
  via CDP e sessão logada -- o sinal parece estar na própria conexão
  automatizada (CDP/Playwright), não em headless, IP ou ausência de login.
  Não existe hoje um transporte já validado no projeto capaz de contornar
  isso sem técnica de evasão (stealth, CAPTCHA solver, fingerprint).
- **Decisão:** `TASK-104C` fica formalmente **adiada** (não cancelada) --
  nenhum código de provider foi escrito; login humano não é reproduzido
  automaticamente em nenhum fluxo. Fica para estudo futuro quando houver
  uma abordagem sem evasão (ex.: API oficial/parceria, ou mudança de
  comportamento da própria Shopee). Nenhuma técnica de evasão foi
  cogitada ou implementada.
- **Fora de escopo, mantido de propósito:** resolver o CAPTCHA (manual ou
  automatizado), stealth, spoof de fingerprint, proxy, rotação de IP,
  cookies copiados. Credencial usada no teste não foi registrada em
  código, log ou documentação.

## DEC-091 — Mercado Livre usa Edge/CDP somente como fallback final

- **Data:** 2026-08-22.
- **Decisão:** TASK-104B mantém Playwright normal/headed como aquisição
  primária. Somente bloqueio, circuito aberto ou falha de navegação admite uma
  tentativa pelo Edge/CDP loopback já supervisionado; não há retry próprio.
- **Desacoplamento:** os dois transportes entregam a mesma `Page` ao mesmo
  `MercadoLivreProvider.extract()`. Edge/CDP não conhece seller, condição,
  avaliação, identidade, relevância ou ranking.
- **Carga:** sucesso primário nunca toca o Edge. O fallback abre uma página,
  coleta todos os cards necessários e encerra somente essa página; não cria nem
  encerra o Edge dedicado. Enriquecimento de detalhe reúne seller, entrega,
  condição, disponibilidade e avaliação na mesma abertura comum e limitada.
- **Falha segura:** endpoint CDP é restrito a loopback, timeouts são explícitos,
  falha final fica isolada na origem Mercado Livre e não bloqueia outras lojas.
- **Validação real:** por decisão operacional, será feita uma única abertura
  final no Edge, depois de toda preparação offline.

## DEC-090 — Magalu separa transporte do parser SSR e admite Edge/CDP loopback

- **Data:** 2026-08-22.
- **Decisão:** TASK-104A separa aquisição, parser e enriquecimento. O provider
  recebe uma porta `MagaluSearchTransport`; quando configurado, o adapter atual
  conecta a um Edge normal via CDP loopback e entrega o HTML final ao mesmo
  parser `#__NEXT_DATA__`. Edge/CDP é o único transporte operacional desta
  versão; HTTP e Playwright foram removidos do fluxo Magalu.
- **Auditoria:** a carga inicial observada não chamou API pública separada de
  catálogo. O endpoint Next derivável do `assetPrefix` também respondeu 403 no
  runtime do backend; não será tratado como contrato estável.
- **Falha segura:** erro no enriquecimento preserva os resultados básicos e a
  falha da fonte continua isolada por claim no orquestrador. Ausência de nota e
  quantidade permanece `NULL`.
- **Segurança:** configuração rejeita CDP remoto, público, HTTPS, sem porta ou
  com credenciais; somente `127.0.0.1`, `localhost` e `::1` são aceitos. Não há
  headers especiais, stealth, fingerprint, CAPTCHA, proxy ou evasão.
- **Validação real:** Edge 151 normal/CDP loopback retornou HTTP 200; parser SSR
  encontrou 39 itens e o provider real devolveu múltiplas ofertas completas.
  HTTP direto/Playwright gerenciado seguem bloqueados, mas já não são requisito
  quando o transporte CDP está configurado.
- **Operação:** o worker mantém um supervisor dedicado, perfil próprio e CDP
  loopback; reinicia o Edge após queda. Timeouts separados limitam conexão,
  navegação, documento SSR e leitura. Erros não são classificados como
  navegação transitória, evitando retry agressivo e preservando isolamento.
- **Smoke real:** Edge ausente iniciou automaticamente; após encerrar os 8
  processos do perfil dedicado, o supervisor recuperou com novo PID e a busca
  posterior retornou 20 ofertas.

## DEC-089 — Expansão de lojas dividida em TASK-104A/B/C

- **Data:** 2026-08-22.
- **Decisão:** dividir a expansão em TASK-104A Magalu, TASK-104B Mercado Livre
  e TASK-104C Shopee, permitindo implementação e validação real independentes
  sem duplicar domínio ou consumidores.
- **Magalu/Mercado Livre:** `platform` somente quando houver evidência
  explícita de venda pela própria plataforma; parceiro e fulfillment são
  classificados separadamente, e ausência permanece `unknown`.
- **Shopee:** vendedor oficial depende do selo explícito da página e será um
  atributo próprio, tri-state, do `Seller`; nunca será confundido com
  `seller_kind=platform` ou entrega pela Shopee.
- **Navegação:** vendedor, entrega, condição e avaliação aproveitam a mesma
  abertura/enriquecimento da oferta. Nenhuma loja ganha navegação adicional
  exclusiva para esses campos.
- **Ordem:** 104A, 104B e 104C; cupons vêm depois e TASK-098 permanece no fim.

## DEC-088 — Expansão de lojas precede cupons na V1.2

- **Data:** 2026-08-22.
- **Decisão:** após a TASK-103, a próxima prioridade é integrar Magalu,
  Mercado Livre e Shopee pela arquitetura comum de Store Providers. Pesquisa
  de cupons passa ao item seguinte.
- **Limites preservados:** AliExpress continua fora da V1.2 e a TASK-098
  permanece reservada como último item da versão.

## DEC-087 — Comparação exige Product específico resolvido e ownership por Offer

- **Data:** 2026-08-22.
- **Decisão:** TASK-103 compara somente Offers que apontam para o mesmo
  `Product.id` com `identity_key` resolvida pela TASK-097. Título, família,
  missão e IA nunca definem equivalência.
- **Acesso:** Offer âncora e candidatas exigem relevância `MATCH` ou
  `POSSIBLE_MATCH` em missão do USER; `NO_MATCH` e missão alheia falham
  fechados.
- **Comercial:** última observação e avaliação da própria origem; até cinco por
  loja, com ordenação determinística equivalente à pré-lista.
- **Sem expansão:** nenhuma migration, coleta, histórico ou gráfico.

## DEC-086 — Operações ADMIN usam serviços lógicos, não o runtime Docker

- **Data:** 2026-08-22.
- **Decisão:** TASK-102 reúne dashboard, administração e operações rotineiras.
  UI/backend usam `ServiceOps` com allowlist lógica e nunca aceitam shell,
  container ou comando arbitrário.
- **Runtime atual:** controlador independente assinado, rede privada e
  socket-proxy restrito; somente `collection_worker` e `telegram_notifier`.
- **Evolução:** Docker é adapter substituível. Instalação direta futura troca
  somente por `WindowsServiceOpsAdapter`/supervisor equivalente.
- **Dados:** remoção de usuário usa tombstone e preserva todo histórico; API
  keys ficam estruturadas, porém emissão e autenticação seguem desabilitadas.

## DEC-085 — Minha conta edita somente dados existentes da própria sessão

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-101 e item 10 da V1.2.
- **Decisão:** `/app/account` usa exclusivamente o `User` resolvido pela
  `WebSession`; a API não recebe `user_id`. Perfil e notificações permanecem
  separados pelas permissões já existentes e todos os métodos mutáveis herdam
  CSRF de `require_web_session`.
- **Reuso:** nome, e-mail, lojas/categorias preferidas e os dois flags de
  notificação são os campos atuais de `User`. A Web e o Telegram passam a
  editar/consumir o mesmo estado persistido.
- **Extensibilidade:** opções válidas são fornecidas pelo backend, sem catálogo
  paralelo no React.
- **Vínculo opcional:** a Web emite uma challenge de alta entropia, armazena só
  o hash SHA-256 por 10 minutos e a consome uma única vez após `/vincular` no
  chat privado autenticado do próprio Telegram. O cliente Web nunca fornece IDs
  Telegram. Desvincular revoga apenas o canal Telegram e preserva a WebSession,
  conta, missões e ofertas.
- **Persistência mínima necessária:** `telegram_link_tokens` é exclusiva para a
  prova de posse; não é uma segunda identidade nem duplica `User`.
- **Fora de escopo:** IA, username, papel, senha e exclusão de conta.

## DEC-084 — Área USER de ofertas usa EXISTS sobre relevância do proprietário

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-100 e item 9 da V1.2.
- **Decisão:** `/app/offers` lista Offers já acessíveis ao USER por
  `MissionOfferRelevance → Mission.user_id`, aceitando somente `MATCH` e
  `POSSIBLE_MATCH`. A consulta usa `EXISTS`, não join de saída, para uma Offer
  ligada a várias missões aparecer uma única vez.
- **Estado comercial:** filtros e cards usam somente a última
  `PriceObservation` da própria Offer. Ordenação/paginação são determinísticas.
- **Sem expansão:** nenhuma coleta, tabela, migration, IA, comparação ou
  agregação global de produto. O detalhe continua sendo a TASK-095.

## DEC-083 — Pesquisa Web read-only; missão somente após “Monitorar”

- **Data:** 2026-08-22.
- **Classificação:** implementar agora, como TASK-099 e item 8 da V1.2.
- **Correção:** o desenho inicial criava missão no ato de pesquisar e foi
  rejeitado antes da aprovação. `/app/search` agora consulta de forma read-only
  somente `Product`, `Offer` e a última `PriceObservation` persistidos.
- **Decisão:** pesquisar nunca cria missão, run, observação ou coleta. Após ver
  resultados e variantes, somente “Monitorar” chama o endpoint/service de
  criação existente. Famílias permitem uma, várias ou todas as variantes;
  categorias genéricas continuam válidas sem identidade forçada.
- **Justificativa:** separa exploração de monitoramento sem criar scraper, fila,
  tabela ou estado temporário paralelo e preserva integralmente os três fluxos
  determinísticos da TASK-097.
- **Sem mudança:** providers, IA, Telegram, schema e produção não foram
  alterados. A área geral de ofertas permanece no item 9.

## DEC-082 — Priorizar toda a Web restante antes da comparação

- **Data:** 2026-08-22.
- **Decisão:** adiar comparação entre lojas e executar primeiro o bloco Web
  ainda ausente. A próxima atividade passa a ser pesquisa de produtos pelo site.
- **Auditoria local:** hoje existem `/app`, missões, criação/detalhe de missão e
  detalhe de oferta; `/admin` possui somente a casca inicial. Ainda faltam a
  pesquisa, a área geral de ofertas, minha conta e as áreas administrativas.
- **Nova ordem:** pesquisa vira item 8; ofertas USER, item 9; minha conta, item
  10; dashboard DEV/ADMIN, item 11; administração de dados, item 12; controles
  operacionais, item 13. Comparação entre lojas fica no item 14. Cupons, novas
  lojas e histórico externo passam aos itens 15, 16 e 17. TASK-098 continua
  como último item, agora o 18.
- **Próxima ação:** formalizar a pesquisa de produtos pelo site com o próximo
  número global quando o usuário mandar iniciar.

## DEC-081 — Mover a TASK-098 para o fim da V1.2

- **Data:** 2026-08-22.
- **Decisão:** manter a TASK-098 formalizada e com o mesmo número, mas adiar sua
  execução para o último item da V1.2.
- **Nova ordem:** a ordem aqui registrada foi posteriormente substituída pela
  `DEC-082`; TASK-098 continua sendo o último item da fase.
- **Justificativa:** o usuário decidiu deixar gráficos para o fechamento da
  fase. A dependência técnica da identidade global da TASK-097 continua
  atendida e o escopo interno da TASK-098 não muda.
- **Próxima ação:** substituída pela `DEC-082`, que prioriza a Web restante.

## DEC-080 — Reordenar a V1.2: lojas Magalu/Mercado Livre/Shopee; autenticação comercial e lives na V2

- **Data:** 2026-08-22.
- **Decisão:** remover da V1.2 a consulta autenticada de frete/parcelamento,
  inclusive a modalidade restrita a DEV/ADMIN, e mover integralmente essa
  capability para a V2. Mover também a pesquisa de ofertas em lives para a V2.
- **Novas lojas na V1.2:** o item de expansão de fontes passa a abranger Magalu,
  Mercado Livre e Shopee, cada uma como Store Provider aderente à arquitetura
  comum. AliExpress não entra nesta etapa e permanece futuro.
- **Contagem e ordem:** com a retirada de dois itens, a V1.2 volta de 18 para
  16 itens. O item de novas lojas passa a ser o 15 e menor preço histórico
  externo passa a ser o 16. TASKs já formalizadas até a TASK-098 não mudam.
- **Separação de escopo:** o provider público da Shopee na V1.2 não inclui
  Shopee Live. Coleta autenticada e lives exigem desenho operacional e de
  segurança próprio na V2.
- **Próxima ação:** esta indicação foi posteriormente substituída pela
  `DEC-081`, que moveu a TASK-098 para o fim da V1.2.

## DEC-079 — Separar identidade global (TASK-097) de histórico e gráficos (TASK-098)

- **Data:** 2026-08-22.
- **Decisão:** a TASK-097 passa a tratar exclusivamente identidade global de
  produto/variante e resolução determinística de pedidos específicos/genéricos.
  A TASK-098 fica formalmente reservada para histórico e gráficos e depende da
  identidade concluída pela TASK-097.
- **Identidade:** evoluir `Product` para variante global referenciada pelas
  `Offer`, com chave versionada formada por categoria, marca, família, modelo,
  variante e atributos normalizados relevantes. Cada categoria declara os
  atributos obrigatórios; ausência não é wildcard e impede união entre lojas.
- **Interação:** pedido específico rejeita chave diferente. Pedido genérico
  é distinguido entre `PRODUCT_FAMILY`, que apresenta variantes deduplicadas
  para escolha única, múltipla ou todas na Web e no Telegram, e
  `GENERIC_CATEGORY`, que continua válida sem escolha obrigatória e nunca une
  produtos distintos. IA pode interpretar a entrada inicial, nunca equivalência,
  ordenação ou seleção final. A TASK-098 só compara `SPECIFIC_PRODUCT` resolvido.
- **Compatibilidade:** nenhum `Product` antigo é unido somente pelo título.
  Backfill ocorre apenas com identidade completa determinística; ambiguidades
  permanecem não resolvidas e missões/ofertas/histórico existentes são
  preservados.
- **Próxima ação:** implementar somente a TASK-097. Não antecipar endpoints,
  consultas, métricas ou gráficos da TASK-098.

## DEC-078 — TASK-096: avaliações pertencem à Offer/Store e compartilham a abertura de detalhe

- **Data:** 2026-08-22.
- **Decisão de domínio:** persistir em `Offer` o snapshot atual completo
  (`rating_average`, `review_count`, `rating_observed_at`). Não é nota global de
  Product e não integra `PriceObservation`, pois sua mudança não representa
  mudança de preço/estado comercial nem deve criar observações redundantes.
- **Evidência:** aceitar somente nota e contagem explicitamente declaradas em
  card, JSON-LD `AggregateRating` ou microdata. Ausência/ambiguidade não apaga
  snapshot anterior; contagem abreviada não é convertida em número exato.
- **Navegação e extensibilidade:** `enrich_offer_details` combina vendedor,
  condição, parcelamento e avaliação na mesma abertura, no máximo uma vez por
  oferta. Terabyte permanece só-card sob o bloqueio atual. Uma loja nova adere
  pelo contrato Raw e hooks do provider, sem mudanças nas camadas consumidoras.
- **Apresentação:** página USER e Telegram mostram a origem; sem textos de
  reviews, histórico de notas, agregação, IA ou influência no ranking.

## DEC-077 — TASK-095: primeira página rica é centrada em Offer e exige relevância ligada a missão do usuário

- **Data:** 2026-08-22.
- **Decisão:** criar `GET /api/v1/offers/{offer_id}` e
  `/app/offers/{offer_id}` sobre a `Offer` existente. `Product` fornece o
  título, mas não é raiz da página: a coleta atual cria uma Product por nova
  Offer e ainda não existe canonicalização global suficiente para uma página
  agregada de produto.
- **Ownership:** WebSession + `MISSION_READ`; o SQL exige uma
  `MissionOfferRelevance` `MATCH`/`POSSIBLE_MATCH` ligada a uma `Mission` do
  usuário. `NO_MATCH`, recurso inexistente e recurso alheio são indistinguíveis
  na resposta fail-closed.
- **Snapshot comercial:** usar a última `PriceObservation` por
  `observed_at DESC, id DESC` e somente suas opções de parcelamento. Reutilizar
  Product/Offer/Store/Seller; não expor evidência bruta ou IDs operacionais.
- **Escopo:** detalhe individual e links pelas missões. Sem migration, tabela,
  IA, coleta, reviews, gráficos, comparação entre lojas, cupons ou admin.

## DEC-076 — TASK-094: pré-lista seleciona até cinco ofertas relevantes por loja com ranking comercial determinístico

- **Data:** 2026-08-22.
- **Decisão:** a pré-lista deixa de representar cada loja por seu menor preço
  absoluto e passa a carregar até cinco ofertas por loja. A ordem final é
  relevância persistida (`MATCH` antes de `POSSIBLE_MATCH`), condição
  (`new` > `refurbished` > `used` > `unknown`), vendedor
  (`platform` > `marketplace_partner` > desconhecido), disponibilidade,
  preço/valor total e identificador estável. `NO_MATCH` nunca entra e
  relevância pendente continua bloqueando a primeira publicação.
- **Pool intermediário:** todos os providers usam a mesma pré-seleção de até
  oito candidatos por loja. Foi removido o colapso exclusivo da Amazon para o
  menor preço; oito preserva diversidade suficiente para o top 5 sem enviar os
  até 20 cards de uma loja para a classificação já existente.
- **Condição histórica:** `OfferCondition` percorre
  `RawCollectedOffer` → `NormalizedCollectedOffer` → `PriceObservation`.
  Só evidência explícita do provider classifica recondicionado/usado. Na Amazon,
  a validação real confirmou a regra da plataforma: ausência desses marcadores
  na oferta principal significa `new`; demais providers continuam `unknown`
  sem evidência. A migration `20260822_0002` adiciona o
  campo não nulo com backfill conservador, e a equivalência comercial da
  TASK-093 passa a considerar condição.
- **Eventos e entrega:** novas publicações usam
  `mission.prelist_ready.v2`/`mission.prelist_errata.v2`, com coleção ordenada
  de snapshots reais. V1 permanece aceito. O Telegram agrupa normalmente uma
  mensagem por loja, repartindo apenas pelo limite técnico; a errata reutiliza
  exatamente o mesmo ranking comercial e não considera uma oferta usada mais
  barata uma melhora sobre uma nova.
- **Roadmap:** esta melhoria torna-se o item 4 da V1.2, antes da página rica de
  produto/oferta. O escopo passa de 16 para 17 itens. A ausência do arquivo
  formal da TASK-093 foi apenas registrada, sem reconstrução retroativa.
- **Classificação:** evolução funcional da V1.2, com migration e eventos V2;
  sem nova tabela paralela, nova decisão por IA ou mudança em produção.

## DEC-075 — TASK-092: gerenciamento de missões pela web reaproveita `app.missions` sem nenhuma regra nova; sessão assíncrona própria; correção de `actor_type` na auditoria

- **Data:** 2026-08-22.
- **Ideia:** item 2 da V1.2 -- levar criar/listar/detalhar/editar/pausar/
  retomar/cancelar missão para `/app`, reaproveitando inteiramente o
  domínio já existente (`app.missions.service`/`app.missions.query`),
  nunca um segundo sistema de missões. Preflight com o usuário decidiu
  dois pontos: cancelamento exige confirmação no cliente (React), sem
  mudança de backend/domínio; listagem padrão mostra ativas + pausadas,
  com filtro de status cobrindo todos os estados + "todas".
- **Endpoints chamam só o serviço existente:** `backend/app/webapp/missions_router.py`
  (7 endpoints sob `/api/v1/missions`) não implementa nenhuma regra de
  negócio própria -- só monta request/response em torno de
  `create_mission_from_criteria_async`/`transition_mission_async`/
  `edit_mission_criteria` (já existentes, usados pelo webhook Telegram
  desde a TASK-079/TASK-069) e de 3 funções novas em `app.missions.query`
  (`list_missions_for_user_by_status`, `count_missions_for_user_by_status`,
  `get_mission_detail_for_user` -- leitura pura, mesma camada). Toda
  autenticação/CSRF vem de `Depends(require_web_session)` (TASK-091/
  DEC-074) -- nenhuma configuração extra por endpoint.
- **`get_web_async_session` (novo, `app.database.dependency`):** os
  endpoints web chamam funções assíncronas de missão, mas `webapp/router.py`
  (TASK-091) só tinha sessão síncrona. Em vez de reaproveitar
  `get_telegram_async_session` (que propositalmente NÃO comita automático,
  porque o webhook Telegram precisa controlar a fronteira de transação
  em torno de `await`s de IA/Telegram -- TASK-079), a nova dependência
  reaproveita o mesmo engine assíncrono de processo
  (`get_telegram_async_engine`, sem pool novo) mas comita automaticamente
  no sucesso, como `get_session`: os endpoints de missão não têm nenhum
  `await` de I/O externo no meio da transação, então uma única transação
  por requisição é segura e mais simples.
- **Correção de auditoria encontrada na validação real:** `create_mission_from_criteria(_async)`
  sempre gravava a transição inicial `draft→active` com
  `actor_type="telegram"` hardcoded -- uma missão criada pela web
  aparecia com auditoria incorreta. Corrigido com um parâmetro
  `actor_type: str = "telegram"` (default preserva os dois chamadores
  existentes -- Telegram e `scripts/validate_collection_worker.py`); o
  endpoint web passa `actor_type="web"`. Confirmado no container real
  antes e depois da correção.
- **Classificação:** Segunda interface sobre o domínio de missões já
  existente (TASK-092, item 2 da V1.2) -- nenhuma feature de negócio nova
  além do que o Telegram já faz, nenhuma migration.
- **Justificativa:** o princípio já registrado em `DEC-072`/`v1.2-scope.md`
  ("nunca um segundo sistema de missões") só se sustenta se toda regra de
  negócio ficar de fato numa única camada -- daí a resistência em
  duplicar validação/transição/edição no router web, mesmo quando isso
  significou adicionar pequenas funções de leitura em `app.missions.query`
  em vez de compor queries ad hoc dentro do router.
- **Próxima ação:** nenhuma além da implementação já feita. TASK-092
  aguardando revisão/aprovação do usuário antes do commit.

- **Ideia (rodada 2 — auditoria arquitetural de 21 pontos, 2026-08-22):** o
  usuário revisou a primeira entrega e não aprovou o commit, pedindo uma
  auditoria formal de concorrência, posse, contrato de listagem, UX de
  conflito, agnosticismo de canal e cobertura de teste real (PostgreSQL +
  container). A auditoria confirmou dois bugs reais (não hipotéticos) e
  formalizou quatro pontos que já estavam corretos na prática mas nunca
  tinham sido decididos explicitamente:
  1. **`state_version` -- semântica definitiva corrigida (bug real):**
     `edit_mission_criteria` checava `expected_state_version` mas nunca
     incrementava `mission.state_version` no sucesso -- uma segunda edição
     concorrente na mesma versão nunca era rejeitada (perda silenciosa de
     escrita). A semântica documentada em `docs/architecture/mission-criteria.md`
     ("edição nunca mexe em `status`/`state_version`") descrevia essa lacuna
     como desenho intencional; era proteção incompleta, não escolha. Corrigido:
     `edit_mission_criteria` agora incrementa `state_version` como qualquer
     transição de ciclo de vida -- o campo controla a missão inteira (edição
     de critério + transições), não só o lifecycle. Provado com teste de
     integração real (`test_lost_update_is_prevented_by_state_version`,
     `tests/integration/test_webapp_missions.py`): cliente A edita
     `target_amount` na versão N, cliente B tenta editar `sources` na mesma
     versão N -- B recebe `409`, o valor de A persiste sozinho.
     `docs/architecture/mission-criteria.md` e `docs/database/schema.md`
     atualizados para descrever a semântica corrigida (histórico anterior
     preservado, não apagado).
  2. **`InvalidMissionTransitionError` não tratado no router (bug real,
     introduzido nesta própria TASK):** `_run_command` só capturava
     `MissionNotFoundError`/`MissionVersionConflictError`/
     `MissionTransitionConditionError` -- um comando inválido para o estado
     atual (ex.: `resume` numa missão `cancelled`) levantava
     `InvalidMissionTransitionError`, não capturada, produzindo `500` sem
     detalhe. Corrigido (import + inclusão no tupla de exceções). Só foi
     encontrado porque o usuário pediu explicitamente um teste direto contra
     o endpoint (não só a UI) para `CANCELLED→RESUME`/`CANCELLED→PAUSE` --
     confirma o valor de testar a transição terminal no nível HTTP, não só
     no domínio.
  3. **Posse centralizada:** nova função `get_mission_for_user(session, *,
     user_id, mission_id) -> Mission | None` em `app.missions.query`,
     reaproveitada pelo router web (`_require_owned_mission`) -- ausência e
     posse de outro usuário retornam o mesmo `None`, nunca distinguidos.
     Decisão explícita: **não** migrar os pontos de chamada já existentes do
     Telegram para esta função nesta TASK (código já validado em produção,
     sem necessidade funcional de mexer) -- só o caminho novo (web) usa a
     função nova.
  4. **`actor_type` passa a ser obrigatório (sem default):** o default
     `"telegram"` em `create_mission_from_criteria(_async)` (adicionado na
     primeira rodada desta TASK) foi reavaliado -- um default mascarava
     silenciosamente qualquer chamador futuro que esquecesse de passar o
     valor certo. Todo o código-base já segue essa convenção em toda outra
     função equivalente (`transition_mission_async`, autenticação,
     autorização, privacidade) -- os três chamadores reais (Telegram,
     endpoint web, `scripts/validate_collection_worker.py`) já passavam o
     valor explicitamente, então a correção certa era remover o default, não
     trocar seu valor.
  5. **Contrato de listagem/paginação formalizado:** `limit`/`offset`
     (padrão 20, máximo 100, `422` acima disso), ordenação estável por
     `updated_at DESC` -- já implementado na primeira rodada, agora coberto
     por teste de integração real para cada um dos 6 filtros de status
     (incluindo `expired`, alcançado via transição real `EXPIRE`, nunca
     seed direto).
  6. **UX real de `409` no frontend:** a SPA nunca força a mudança nem
     ignora o conflito -- mostra uma mensagem explicando que a missão mudou,
     recarrega os dados automaticamente e obriga o usuário a revisar antes
     de tentar de novo (`MissionDetailPage.tsx`; formulário de edição
     remonta via `key={mission.state_version}` para não reter estado local
     obsoleto).
- **Validação real (container, 2026-08-22, pós-correções):** imagem
  reconstruída (`docker compose build api`), stack subida com Postgres
  descartável, migrações aplicadas até `20260821_0001` (sem migration nova),
  dois usuários descartáveis criados só para o teste. Confirmado via HTTP
  direto: ciclo completo criar→pausar→editar (`state_version` avança
  2→3)→retomar→cancelar; edição em `ACTIVE` rejeitada (`409`); `resume`/`pause`
  em missão `CANCELLED` rejeitados (`409`, não `500` -- confirma a correção
  do ponto 2 acima); posse indistinguível entre "não existe" e "não é sua"
  (`403 mission_access_denied`, corpo byte-a-byte idêntico); `409` de versão
  obsoleta reproduzido de propósito; filtro padrão exclui cancelada,
  `?status=cancelled`/`?status=all` incluem. Confirmado em navegador real
  (não só `curl`): tela de login, estado vazio ("nenhuma missão encontrada
  para este filtro" -- não é erro), redirecionamento para login quando
  não autenticado, lista populada e troca de filtro pela interface.
  Container e volume descartáveis removidos ao final (`docker compose down
  -v`); nenhum dado de teste ficou para trás.
- **Ajuste final antes do commit (2026-08-22, aprovação do usuário):** a
  ordenação de `list_missions_for_user_by_status` estava em `created_at`
  decrescente (implementação original, nunca formalizada como decisão --
  o ponto 8 da auditoria só pedia "o domínio deve decidir o campo
  correto"). O usuário pediu explicitamente `updated_at DESC, id DESC`:
  pausar/retomar/editar/cancelar devem subir a missão na lista, não só
  criá-la; `id DESC` é o desempate determinístico para `updated_at`
  colidido, necessário para paginação estável por `offset`. Ajustado em
  `app.missions.query.list_missions_for_user_by_status`; teste dedicado
  adicionado (`tests/test_mission_query.py`, assert no SQL compilado).
  Pipeline completo (1414 unitários/90,42%, 73 integração PostgreSQL)
  reexecutado e aprovado após o ajuste.
- **Próxima ação (atualizada):** nenhuma. TASK-092 aprovada pelo usuário
  para commit.

## DEC-074 — Endurecimento da TASK-091: CSRF acoplado a `require_web_session` (não a nenhum router), frontend em TypeScript, whitelist de rotas da SPA, catch-all por qualquer método, empacotamento Docker multi-stage

- **Data:** 2026-08-21/22 (quatro rodadas de revisão do usuário antes do
  commit).
- **Ideia (rodada 1):** antes de aceitar a fundação web (TASK-091) como
  pronta para commit, o usuário pediu uma auditoria de segurança formal
  com 4 pontos bloqueantes: sessão/cookie, CSRF, empacotamento Docker real
  e limpeza de contas de teste. A auditoria confirmou que geração/hash do
  token de `WebSession` (`secrets.token_urlsafe(32)`, 256 bits, só o
  SHA-256 vai pro banco) e os atributos do cookie de sessão (`HttpOnly`,
  `Secure` por ambiente, `SameSite=Lax`, `Path=/`) já estavam corretos
  desde a implementação original; achou e corrigiu 3 lacunas reais: CSRF
  nunca implementado, frontend nunca empacotado na imagem Docker, e uma
  rota de API inexistente caindo incorretamente no catch-all da SPA (200
  `index.html` em vez de 404).
- **Ideia (rodada 2 — 3 correções sobre a rodada 1):** o usuário revisou a
  primeira rodada e não aprovou o commit ainda, apontando 3 problemas
  concretos na própria correção: (a) a decisão de preflight foi React +
  **TypeScript** + Vite, mas a implementação saiu em JavaScript puro
  (`.jsx`/`.js`); (b) CSRF só cobria `POST`/`DELETE` por `Depends` em rota
  individual — `PUT`/`PATCH` de TASKs futuras poderiam nascer sem
  proteção, sem ninguém perceber; (c) o isolamento de `/api/*` usava
  blacklist de prefixos de backend, frágil por construção (uma rota nova
  esquecida na lista vira `200 index.html` silenciosamente) — o usuário
  pediu o inverso, whitelist explícita do que pertence à SPA.
- **Ideia (rodada 3 — escopo do CSRF corrigido de novo):** a rodada 2
  trocou o `Depends` por rota por um middleware ASGI sobre todo
  `POST`/`PUT`/`PATCH`/`DELETE` cujo caminho começasse com `/api/v1/`. O
  usuário apontou que isso era amplo demais: (a) interceptava antes de
  saber se a rota existia, mascarando `404` de rota inexistente como `403
  csrf_invalid`; (b) protegeria erroneamente qualquer endpoint futuro sob
  `/api/v1` mesmo que autenticado por Bearer/service token -- um canal sem
  cookie, sem risco de CSRF, para o qual essa defesa não faz sentido. A
  regra correta é: CSRF protege requisição mutável que depende de
  autenticação automática por cookie da `WebSession` (incluindo o próprio
  login, antes da sessão existir) -- não é uma política global de
  `/api/v1`.
- **CSRF — double-submit cookie girado na fronteira de login, escopado ao
  router da WebSession (não middleware, não `Depends` por rota
  individual):** `require_csrf` (`backend/app/webapp/csrf.py`) é
  registrada **uma única vez**, como dependência do próprio router
  (`app.webapp.router.router = APIRouter(prefix="/api/v1", tags=["webapp"],
  dependencies=[Depends(require_csrf)])`). Esse router É o canal
  WebSession/cookie da aplicação web (login, logout, sessão atual) --
  amarrar a defesa a ele, em vez de a uma string de prefixo de URL,
  resolve as duas lacunas da rodada 2 de uma vez:
  - Rota inexistente nunca chega a nenhum router (FastAPI/Starlette
    resolve `404` antes de qualquer dependência rodar) -- `403` nunca mais
    mascara ausência de rota.
  - Um canal de autenticação diferente (Bearer, service token, webhook do
    Telegram com segredo de header) simplesmente vive em outro router e
    nunca passa por `require_csrf`, mesmo estando montado na mesma
    aplicação.
  - Qualquer endpoint mutável futuro do canal web (edição de missão pela
    SPA, etc.) que for adicionado a este mesmo router (ou a outro que
    também declare a mesma dependência) herda a proteção automaticamente
    -- sem exigir que o desenvolvedor lembre de anotar `Depends(require_csrf)`
    rota por rota; e sem arriscar proteger de mais um canal que não usa
    cookie.
  `require_csrf` ignora `GET`/`HEAD`/`OPTIONS` internamente, já que o
  mesmo router também registra `GET /web-sessions/current`. Cookie
  `aishopping_csrf`, não-`httpOnly` (a SPA precisa ler o valor),
  `Secure`/`SameSite`/`Path` no mesmo padrão do cookie de sessão. Emitido
  de forma anônima (sem sessão) por `register_spa` sempre que a casca
  (`index.html`) é servida e o cliente ainda não tem um — é isso que
  protege o próprio `POST /api/v1/web-sessions` (login) contra CSRF de
  login, já que a SPA sempre carrega a casca antes de qualquer JS rodar.
  `create_web_session` gira o cookie de novo após autenticar (mesmo
  princípio de nunca atravessar uma fronteira de privilégio com um
  identificador reaproveitado, já aplicado à própria `WebSession`).
  Preferido a synchronizer token stateful (exigiria coluna nova em
  `WebSession` e uma consulta a mais por requisição) por já bastar para
  uma SPA same-origin sem introduzir estado adicional no banco.
- **Catch-all da SPA agora casa com qualquer método HTTP, não só `GET`:**
  efeito colateral descoberto ao validar a correção acima contra o
  container real -- com o catch-all registrado só para `GET`, o Starlette
  via o padrão de caminho bater (`/{full_path:path}` casa com qualquer
  string) mas o método não, devolvendo `405 Method Not Allowed` em vez de
  `404` para `PATCH`/`PUT`/`DELETE` numa rota de API inexistente. Não era
  mascaramento de CSRF (o `403` já não acontecia mais depois da correção
  acima), mas também não era o `404` esperado. `register_spa` agora
  registra o catch-all via `app.api_route(..., methods=["GET", "HEAD",
  "POST", "PUT", "PATCH", "DELETE"])` e responde `404` imediatamente para
  qualquer método que não seja `GET`/`HEAD` -- a SPA em si só serve
  navegação `GET`, mas o catch-all precisa "existir" para todos os
  métodos para que o Starlette prefira `404` a `405` quando nenhuma rota
  real casar.
- **Ideia (rodada 4 — CSRF finalmente acoplado à autenticação, não a
  nenhum router):** a rodada 3 amarrou CSRF ao router de `web-sessions`
  (`dependencies=[Depends(require_csrf)]` no `APIRouter`). O usuário
  apontou que isso continuava incompleto pelo motivo oposto ao da rodada
  2: agora era estreito demais -- só protegeria endpoints daquele router
  específico. Um endpoint futuro de missões/ofertas/admin, em outro
  router (o desenho natural conforme a V1.2 crescer), não herdaria nada.
  A regra arquitetural definitiva: **CSRF acompanha a autenticação por
  `WebSession`, não o router nem o domínio funcional onde o endpoint
  mora.**
- **CSRF acoplado à dependência `require_web_session`, não a router
  nenhum:** `app.webapp.dependency` ganhou dois níveis --
  `_resolve_web_session` (interno: só resolve cookie -> hash ->
  `WebSession` -> `User`, `401` se ausente/inválida/expirada/revogada,
  nunca aplica CSRF) e `require_web_session` (pública: depende de
  `_resolve_web_session`, e só para métodos mutáveis chama
  `app.webapp.csrf.validate_csrf`). Qualquer endpoint, de qualquer
  router/módulo, que declare `Depends(require_web_session)` herda
  autenticação por cookie **e** CSRF automaticamente -- a composição da
  árvore de dependências do FastAPI garante a ordem correta sozinha
  (sessão resolvida antes do corpo de `require_web_session` executar,
  então `401` sempre precede `403`, nunca o inverso). O helper interno
  não é exportado para uso fora do módulo -- só a dependência pública, que
  é seguro por padrão.
  `require_admin_web_session` passou a compor sobre `require_web_session`
  (antes compunha sobre o antigo `get_current_web_user`, sem CSRF): um
  endpoint administrativo mutável futuro herda autenticação -> CSRF ->
  autorização ADMIN só por declarar essa dependência, sem implementar nada
  disso de novo.
  `app.webapp.router` deixou de ter `dependencies=[Depends(require_csrf)]`
  a nível de `APIRouter` -- login continua como exceção explícita
  (`Depends(validate_csrf)` só nessa rota, já que ainda não existe
  `WebSession` nesse ponto), logout e a consulta de sessão atual passaram
  a depender de `require_web_session` como qualquer outro endpoint do
  canal web, sem nenhuma configuração específica de router. Efeito
  colateral do logout agora exigir sessão válida via `require_web_session`
  (antes tolerava ausência de cookie como no-op `204`): sem sessão válida,
  a resposta agora é `401 not_authenticated`, nunca mais um sucesso
  silencioso nem `403 csrf_invalid`.
  Prova arquitetural dedicada (`tests/test_webapp_dependency.py`): um
  router propositalmente sem relação nenhuma com `app.webapp.router`
  (simulando missões), com um endpoint usando só
  `Depends(require_web_session)`, exige CSRF do mesmo jeito -- mutação sem
  CSRF -> `403`; com CSRF válido -> chega ao handler. `spa.py` também
  passou a reaproveitar `_resolve_web_session` (em vez de duplicar a
  resolução de sessão) na checagem de `/admin`.
- **Frontend em TypeScript, não JavaScript:** todos os arquivos fonte
  (`.jsx`/`.js` → `.tsx`/`.ts`), `tsconfig.json`/`tsconfig.app.json`/
  `tsconfig.node.json` no padrão do scaffold oficial `react-ts` do Vite,
  `npm run build` agora roda `tsc -b && vite build` (falha o build se
  houver erro de tipo, não só de bundling). Tipos explícitos para o
  usuário da sessão (`WebSessionUser`, `UserRole`), para o cliente HTTP
  (`ApiError`, `request<T>`) e para o contexto de autenticação
  (`AuthContextValue`). Corrige a implementação para bater com a decisão
  de preflight já aprovada — não é uma decisão nova, é a mesma sendo
  cumprida corretamente.
- **Whitelist de rotas da SPA no lugar da blacklist de prefixos de
  backend:** `register_spa` agora testa `full_path` contra
  `_SPA_OWNED_TOP_LEVEL_SEGMENTS = {"", "login", "app", "admin"}`
  (espelha exatamente as rotas de `frontend/src/App.tsx`) — só esses
  caminhos (e seus descendentes via roteamento client-side) viram
  `index.html`; qualquer outro caminho é `404` por padrão, mesmo que
  ninguém tenha atualizado nenhuma lista para incluí-lo. Inverte a
  responsabilidade: antes, uma rota de backend nova exigia lembrar de
  adicioná-la à blacklist para não vazar como `200 index.html`; agora uma
  rota de backend nova simplesmente nunca aparece na whitelist da SPA, e o
  comportamento seguro (`404`) é automático.
- **Fixação de sessão — já coberta pelo desenho original, agora testada
  explicitamente:** `issue_web_session` nunca aceita um identificador
  vindo de fora (não existe parâmetro pra isso na assinatura) e sempre
  revoga toda sessão ativa do usuário antes de emitir a nova. Não havia
  lacuna real; a auditoria adicionou testes que provam isso
  explicitamente (`tests/test_authentication_service_web.py`).
- **Docker — build multi-stage, Node só em build-time:** `Dockerfile`
  movido para a raiz do repositório (antes `backend/Dockerfile`), com um
  estágio `frontend-build` (`node:22-alpine`, `npm ci` + `npm run build`,
  agora incluindo a checagem de tipos TypeScript) cujo `dist/` é copiado
  para o estágio Python final (`COPY --from=frontend-build`). O contexto
  de build dos três serviços que compartilham a imagem (`api`,
  `telegram_notifier`, `collection_worker`) muda de `./backend` para `.`
  (raiz) em `compose.yaml` — nenhuma imagem duplicada, nenhum serviço
  novo. Só o `api` recebe `AISHOPPING_SPA_DIST_DIR=/app/frontend-dist` (só
  ele serve HTTP/SPA); `spa_dist_dir` no `Settings` continua `None` por
  padrão fora do container (resolve o caminho relativo ao checkout
  local). Validado com `docker compose build` real seguido de
  `docker compose up` real contra Postgres containerizado — não só
  `npm run build` no host.
- **Classificação:** Correção/endurecimento de segurança da fundação web
  (TASK-091, item 1 da V1.2) — nenhuma feature de negócio nova, nenhuma
  mudança de escopo além dos pontos pedidos nas quatro rodadas.
- **Justificativa:** cookie de sessão por si só nunca é suficiente contra
  CSRF em nenhuma aplicação autenticada por cookie; o usuário explicitou
  isso como bloqueador antes de aceitar a fundação. O empacotamento Docker
  do frontend não podia ficar para "quando a aplicação for implantada" —
  sem ele a arquitetura aprovada (SPA same-origin servida pelo mesmo
  backend) simplesmente não existe fora do ambiente de desenvolvimento
  local.
- **Próxima ação:** nenhuma — TASK-091 aguardando aprovação do usuário
  para commit com o endurecimento aplicado.

## DEC-073 — Esclarecer três pontos da reorganização da V1.2 (`DEC-072`): DEV/ADMIN exclusivo sem multi-papel, avaliações sempre por origem, `PriceObservation` redundante é semântica

- **Data:** 2026-08-21.
- **Ideia:** o usuário aprovou a direção da `DEC-072` e pediu três ajustes
  documentais pontuais, antes do commit, para evitar ambiguidade
  arquitetural futura — sem mudar a ordem nem o conteúdo de nenhum dos 16
  itens da V1.2.
- **Ajuste 1 (DEV/ADMIN exclusivo, não multi-papel):** a divisão USER x
  DEV/ADMIN da V1.2 (item 1 de `docs/internal/v1.2-scope.md`) continua sendo
  só uma fronteira de rotas (`/app` x `/admin`) sobre a autorização já
  existente (`app.authorization`, papel único `USER ⊂ ADMIN ⊂ DEV`,
  `DEC-034`) — nunca uma reformulação dela. Explicitado que a V1.2 **não**
  introduz `user_roles`, múltiplos papéis simultâneos, hierarquia complexa
  de roles, RBAC avançado nem planos FREE/PLUS/PRO; isso permanece
  integralmente na V2 (`docs/internal/backlog.md`, "Papéis e planos da V2").
  A V1.2 só precisa de autorização suficiente para garantir que USER nunca
  acesse recursos administrativos e que DEV/ADMIN acesse a área
  administrativa exclusiva do desenvolvedor/administrador atual.
- **Ajuste 2 (avaliações sempre por origem):** reforçado no item 5 que
  `rating_average`/`review_count` nunca representam uma avaliação global do
  produto — pertencem sempre à loja/origem da oferta (ex.: Amazon ⭐4,8,
  KaBuM! ⭐4,9, Pichau ⭐4,7, nunca um `RTX 5070 Ti ⭐4,8` único), salvo regra
  de agregação explicitamente aprovada no futuro. A modelagem definitiva
  (colunas, tabela nova ou reaproveitada) permanece decidida só quando a
  TASK for aberta — nenhuma entidade nova inventada agora.
- **Ajuste 3 (`PriceObservation` redundante é semântica, não só preço):**
  corrigida a redação do item 3 para não sugerir "preço igual = não grava
  observação" — critério simplista demais. A regra registrada é evitar
  observação **semanticamente redundante**: uma `PriceObservation` nova é
  necessária quando qualquer parte do estado relevante mudar (preço,
  disponibilidade, moeda, vendedor/fulfillment relevante, condição
  comercial relevante, ou outro estado que o domínio considerar histórico),
  não só o preço isoladamente. Exemplo registrado: mesmo preço com
  disponibilidade diferente (`AVAILABLE` → `UNAVAILABLE`) **não** é
  redundante. A lista definitiva de campos comparados continua dependendo
  de auditar `backend/app/collection/models.py` na própria TASK.
- **Classificação:** Versão futura (esclarecimento documental da `DEC-072`
  — `docs/internal/v1.2-scope.md`; nenhuma TASK criada, nenhuma
  implementação, migration, frontend, endpoint, commit de código, push ou
  redeploy).
- **Justificativa:** os três pontos eram fonte real de ambiguidade
  arquitetural para quando cada item virar TASK — sem o esclarecimento,
  "DEV/ADMIN" poderia ser mal interpretado como um convite a desenhar RBAC
  completo agora, "avaliações" poderia levar a uma nota global inventada
  sem aprovação, e "reduzir `PriceObservation` redundante" poderia virar
  uma deduplicação ingênua por preço que perderia mudanças reais de
  disponibilidade/condição comercial no histórico.
- **Próxima ação:** nenhuma além da documentação já ajustada. Revisão de
  consistência entre `docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md` e este log
  feita nesta mesma rodada, sem alterar nenhum bloco histórico/datado
  anterior.

## DEC-072 — Reorganizar a V1.2 em torno de uma aplicação web completa (USER/DEV-ADMIN); Telegram passa a canal de alertas; itens de e-mail movidos para V2

- **Data:** 2026-08-21.
- **Ideia:** o usuário decidiu reorganizar oficialmente o roadmap: a V1.2
  deixa de ser uma lista solta de evoluções incrementais sobre o Telegram e
  passa a ter como objetivo central transformar o AIShoppingAgent numa
  plataforma web completa de monitoramento e comparação de preços. A
  aplicação web se torna o núcleo da experiência, com duas áreas
  conceituais (`/app/...` para USER, `/admin/...` para DEV/ADMIN) — mesma
  aplicação, mesmo backend, mesmo banco, autorização por papel real no
  backend (nunca só escondida na interface). O Telegram continua existindo
  e continua controlando as mesmas missões, mas sua função central passa a
  ser alertar rapidamente o usuário, deixando de precisar carregar sozinho
  toda a experiência do produto.
- **Classificação:** Versão futura (reorganização de escopo da V1.2 e do
  backlog da V2 — `docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md`; nenhuma
  TASK criada, nenhuma implementação, migration, frontend, endpoint, commit,
  push ou redeploy nesta rodada — só documentação).
- **Justificativa:** o Telegram, como único canal, satura rápido para
  apresentar dado rico (páginas de produto, gráficos de histórico,
  comparação entre lojas, avaliações, dashboard técnico) — a interface de
  chat não é o formato certo para essas necessidades já registradas como
  ideias soltas no backlog (dashboard web, analytics, painel administrativo
  dividido em quatro itens incrementais). Consolidar tudo isso numa única
  aplicação web, com o Telegram reposicionado como canal de alerta,
  resolve a fragmentação sem duplicar o domínio (missões, ofertas,
  histórico, coleta continuam sendo os mesmos, servidos por uma segunda
  interface). A separação USER/DEV-ADMIN pela mesma aplicação (em vez de
  dois sistemas) evita duplicar autenticação/autorização/sessão e mantém a
  matriz de permissão fail-closed já existente (`app.authorization`,
  `DEC-034`) como única fonte de verdade, sem depender da interface para
  esconder recursos administrativos.
- **Itens preservados, renumerados dentro da nova ordem da V1.2** (nenhuma
  ideia já aprovada foi descartada): redução de `PriceObservation`
  redundante (`DEC-053`), Magalu como quinta loja (`DEC-054`), comparação
  de menor preço histórico externo/interno estilo Steam Inventory Helper
  (`DEC-056`), pesquisa de ofertas em lives — YouTube e Shopee Live
  (`DEC-056`), pesquisa de cupons e consulta autenticada de frete/
  parcelamento restrita a DEV/ADMIN (`DEC-045`). O painel administrativo,
  antes desmembrado em quatro itens incrementais e ainda tratado como
  conceito à parte, passa a ser simplesmente a área `/admin` da mesma
  aplicação web — mesma ideia, sem retrabalho, só sem mais precisar de uma
  seção própria separada da V1.2.
- **Itens removidos da V1.2, movidos para V2** (nenhum descartado, só
  adiado): opt-in de notificação por e-mail no cadastro e notificações por
  e-mail de fato — ambos exigiam a V1.2 original pedir e-mail no `/cadastro`
  antes de qualquer entrega de valor por e-mail; com o novo núcleo da V1.2
  sendo a aplicação web (que não depende de e-mail para existir), o usuário
  decidiu adiar toda a capability de e-mail inteira para a V2, unificada com
  a confirmação/verificação de e-mail que já estava lá.
- **Princípios arquiteturais registrados para a V1.2** (a valer quando cada
  item virar TASK): (1) web, Telegram e futuros clientes usam o mesmo
  domínio/backend sempre que possível; (2) nunca duplicar regras de missão
  entre Telegram e web; (3) nunca duplicar `Offer`/`PriceObservation` numa
  tabela de "anúncio" só para apresentação — a página de produto é
  construída sobre os dados reais já existentes; (4) dados financeiros vêm
  sempre dos collectors/banco, nunca da IA; (5) gráficos e métricas
  históricas (menor/maior preço do período, média, variação percentual) são
  determinísticos, calculados localmente pelo backend/PostgreSQL, nunca por
  IA; (6) avaliações (`rating_average`/`review_count`) permanecem vinculadas
  à loja de origem, nunca misturadas numa nota global sem decisão explícita
  futura; (7) USER e DEV/ADMIN têm autorização real checada no backend,
  nunca só escondida na interface; (8) operações administrativas perigosas
  (ex.: restart de PostgreSQL) recebem proteção maior que operações de
  menor risco (ex.: restart de um worker); (9) qualquer recurso que aumente
  muito o scraping (ex.: avaliações, histórico externo) é avaliado
  criticamente antes de implementar — por isso a V1.2 começa só com
  `rating_average`/`review_count`, sem coletar texto de review individual;
  (10) cada um dos 16 itens da nova V1.2 só vira TASK quando o usuário pedir
  explicitamente — esta reorganização não abre nenhuma TASK sozinha.
- **Próxima ação:** nenhuma. Documentação atualizada
  (`docs/internal/v1.2-scope.md`, `docs/internal/backlog.md`,
  `docs/internal/roadmap.md`, `docs/internal/project-context.md`); aguardar
  revisão do usuário e só então, se solicitado, abrir a primeira TASK (item
  1, fundação da aplicação web) pelo workflow oficial (`AGENTS.md`).

## DEC-071 — Comandos manuais de missão reaproveitam a seleção múltipla da TASK-085; edição encadeia direto no menu após pausar

- **Data:** 2026-08-21.
- **Classificação:** Correção de bug real (pausa sem retomada) e extensão
  de UX determinística, sem introduzir IA em nenhum ponto novo.
- **Contexto:** usuário reportou que editar uma missão `ACTIVE` a deixava
  `PAUSED` sem nenhuma forma de voltar a `ACTIVE`, que não havia comando
  manual dedicado para pausar/retomar, e que `/cancelar_missao` só
  cancelava uma missão por vez.
- **Decisão (reuso de infraestrutura):** `/pausar` e `/retomar` (novos) e
  `/cancelar_missao` (reescrito) passam a compartilhar o mesmo caminho
  local determinístico, reaproveitando integralmente
  `stage_mission_command`/`stage_mission_command_choice`/
  `parse_multi_numbered_choice` já construídos pela TASK-085 — até esta
  TASK, essa infraestrutura só era alcançada pelo caminho ambíguo via IA.
  Nenhuma máquina de estados nova foi criada; o código próprio antigo de
  seleção única de `/cancelar_missao` foi removido, não duplicado.
- **Decisão (UX de `/editar_missao`):** pausar uma missão `ACTIVE` para
  poder editá-la (pré-condição já existente do domínio) deixou de
  terminar numa mensagem pedindo para reenviar `/editar_missao` — agora
  encadeia diretamente no menu de edição na mesma resposta. Um campo
  novo, `auto_paused`, propagado por todo o `pending_intent` da edição,
  distingue essa origem de uma missão que já estava `PAUSED` antes,
  apenas para variar o texto final; nenhum dos dois casos retoma a
  missão sozinho. É uma "solução mínima coerente" (só mais um campo no
  JSON existente), não uma máquina de estados nova.
- **Garantia mantida:** os três comandos manuais e a edição continuam
  100% determinísticos — comprovado por teste com adapter poison-pill
  (`AssertionError` se a IA for chamada) e `assert adapter.calls == []`
  explícito.
- **Fora de escopo, mantido de propósito:** alerta de preço-alvo
  notificando repetidamente sem queda real e busca sem correspondência
  (`iphone 16 512` vs. oferta real da Amazon) — `alerts/evaluator.py` e
  `collection/model_matching.py` não foram tocados. Ver
  `docs/tasks/TASK-090.md`.

## DEC-070 — Desativar Terabyte temporariamente e simplificar parcelamento para só-card

- **Data:** 2026-08-20.
- **Classificação:** Reação operacional a bloqueio externo confirmado
  (Cloudflare Bot Management), reduzindo o escopo da TASK-089 só para a
  Terabyte.
- **O que mudou:** diagnóstico dedicado (sem tentativa de evasão)
  confirmou `server: cloudflare`, `cf-mitigated: challenge`, cookie
  `__cf_bm` em homepage/busca/produto igualmente; Chrome comum, mesmo
  IP do servidor, carrega o site normalmente enquanto o Playwright do
  coletor recebe 403 -- aponta para característica do navegador
  automatizado, não do IP isolado. Volume de requisições subiu 2-7x
  entre 17-19/08 (mesma janela em que a TASK-089 passou a abrir até 3
  páginas individuais por busca para parcelamento detalhado), mas o
  bloqueio só começou em 20/08 05:00, mais de 2 dias depois -- fator
  agravante possível, não causa direta comprovada.
- **Decisão:** (1) `stores.is_active=false` para Terabyte -- mecanismo já
  existente, reversível, não apaga histórico/missões/`mission_sources`;
  (2) `TerabyteProvider.resolve_installment_options` removido -- a
  Terabyte não abre mais página individual só para a tabela detalhada de
  parcelamento (1x-18x); o parcelamento passa a vir só do card da busca,
  mesmo caminho já usado por Amazon/KaBuM!, sempre `is_highlighted=true`.
  Reduz de até 4 navegações (1 busca + até 3 páginas) para 1 por
  execução.
- **Fora de escopo, mantido de propósito:** nenhuma técnica de evasão
  (stealth, spoof de fingerprint, proxy, rotação de IP, CAPTCHA solver,
  cookies humanos, login) foi considerada ou implementada. Pichau, Amazon
  e KaBuM! não foram alterados -- Pichau mantém o enriquecimento
  individual completo (não apresentou o problema); Amazon/KaBuM! já
  usavam só o card.
- **V2 registrada:** investigação detalhada de faixas de parcelamento da
  Terabyte (1x-18x) fica para quando houver solução ao bloqueio que não
  envolva evasão (ex.: parceria/API oficial) -- decisão de produto, fora
  do escopo técnico. Ver `docs/tasks/TASK-089.md`, seção "Terabyte
  desativada e simplificada".
- **Atualização (TASK-105, 2026-08-22):** o diagnóstico acima permanece
  válido -- o bloqueio identificado em 20/08 foi real e não foi contornado.
  O que mudou é o transporte: diagnóstico repetido no DEV confirmou que o
  mesmo Chromium gerenciado pelo Playwright continua bloqueado, mas um
  Edge normal via CDP loopback (mesmo padrão já em produção para a
  Magalu, sem stealth/spoof/proxy) passa limpo -- busca real, 300 cards,
  `TerabyteProvider.extract()` e `resolve_product_availability` atuais
  (sem nenhuma alteração de parser) funcionaram sem bloqueio, inclusive
  em página individual. `TerabyteProvider` passou a usar
  `CdpPageFallback` (mesma infraestrutura CDP/Edge supervisionado da
  Magalu, sem supervisor/porta próprios) como transporte primário e
  único -- sem fallback de volta ao Playwright, que continua
  comprovadamente bloqueado. Ver `docs/tasks/TASK-105.md`.
- **Fechamento (TASK-105, 2026-08-22):** a desativação temporária terminou.
  Migration `20260822_0009_reactivate_terabyte.py` marca
  `stores.is_active=true` para `code='terabyte'` -- mesmo mecanismo
  reversível já usado para desativar (`downgrade()` restaura `false`),
  agora automatizado por Alembic em vez de `UPDATE` manual: a Terabyte
  sobe ativa em qualquer ambiente que rode `alembic upgrade head` a
  partir desta revisão, sem intervenção manual em produção. A variável de
  configuração também deixou de ser exclusiva da Magalu --
  `Settings.magalu_cdp_url` foi renomeada para `Settings.edge_cdp_url`
  (nome antigo/env `AISHOPPING_MAGALU_CDP_URL` continua aceito por
  compatibilidade), já que o mesmo Edge/CDP supervisionado agora atende
  Magalu, Mercado Livre e Terabyte.

## DEC-069 — Corrigir a modelagem de parcelamento da TASK-089 para relação 1:N

- **Data:** 2026-08-17.
- **Classificação:** Correção arquitetural da TASK-089/DEC-068, antes de
  qualquer código ter sido consolidado (a investigação real de campo mudou
  o entendimento do problema).
- **O que mudou:** a investigação real nas quatro lojas (Pichau, Terabyte,
  Amazon, KaBuM!) mostrou que uma oferta pode ter **várias** condições de
  parcelamento simultâneas, não uma só. Pichau expõe 1x-6x com desconto
  (percentual variável por produto, nunca fixo) mais um "12x sem juros"
  padrão com total explícito; Terabyte expõe uma faixa completa 1x-18x com
  desconto decrescente nas primeiras parcelas, "sem juros" nas
  intermediárias e **juros reais** a partir de certa quantidade. O desenho
  original de DEC-068 (`installment_total_amount`/`installment_count`/
  `installment_amount` escalares direto em `Offer`/`PriceObservation`)
  representa só UMA condição — insuficiente e, se implementado, teria
  descartado a maior parte da evidência real encontrada.
- **Decisão arquitetural:** os três campos escalares são substituídos por
  uma entidade dedicada, `OfferInstallmentOption`, em relação 1:N —
  vinculada a `PriceObservation.id` (não a `Offer.id` direto), pelo mesmo
  motivo histórico/append-only que já rege `PriceObservation`: o "estado
  atual" das opções é sempre o da observação mais recente da oferta,
  nunca por UPDATE/DELETE/flag. Amazon e KaBuM! continuam gerando no
  máximo uma opção por oferta (só o que o card mostra); Pichau e Terabyte
  podem gerar várias.
- **Guardrails que continuam valendo, agora por opção:** nunca calcular
  `installment_total_amount` a partir de `count * amount`; nunca inferir
  desconto, juros, ou usar preço riscado/"De:" como total; percentuais de
  desconto lidos sempre do texto atual da página, nunca fixados no código
  (a investigação encontrou percentuais diferentes até no mesmo produto
  Pichau, dependendo de ter ou não o selo promocional "Desconto em Até
  Nx"); ausência de parcelamento nunca invalida a oferta.
- **Sem migration destrutiva:** como nenhum código da DEC-068 havia sido
  consolidado ainda, a migration criada (`20260817_0001`) já nasce com o
  modelo 1:N — não existiu uma migration anterior de 3 campos para
  reverter.
- **Fora de escopo mantido (rodada de modelo):** nenhuma alteração em
  mensagens do Telegram/apresentação nesta rodada — só investigação,
  modelo, migration, contratos, providers e persistência. Ver
  `docs/tasks/TASK-089.md`.
- **Atualização (2026-08-17, rodada de apresentação):** o escopo acima
  foi retomado na mesma TASK-089 — alertas e pré-lista agora mostram
  `💰 À vista`/`💳 Parcelado` dinamicamente, com `is_highlighted` extra
  em `OfferInstallmentOption` para saber qual opção a loja destacou no
  card. Interpretação de "quero em Nx" pelo usuário e qualquer alteração
  no `IntentInterpreter`/fluxo de compra foram explicitamente adiadas
  para uma V2 — ver seção "V2" em `docs/tasks/TASK-089.md`.

## DEC-068 — Separar preço à vista e parcelado da classificação de vendedor

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP, formalizada como TASK-089.
- **Ideia:** coletar e apresentar, quando publicamente disponíveis no card ou
  página já usada pelo provider, preço à vista e preço parcelado, incluindo
  quantidade e valor das parcelas quando houver evidência explícita.
- **Justificativa:** o modelo atual possui um único `PriceObservation.amount`;
  a mudança atravessa os quatro providers, contrato bruto, normalização,
  persistência histórica, migrations, comparação/alertas e Telegram. Acoplá-la
  à TASK-077 impediria que a classificação de vendedor fosse entregue e
  validada isoladamente.
- **Guardrails:** não inferir parcelamento; não substituir silenciosamente a
  semântica vigente de `amount`; preservar histórico; distinguir ausência de
  informação de preço não aplicável; investigar evidência real por loja antes
  de congelar seletores.
- **Decisão arquitetural:** `PriceObservation.amount` permanece o preço à vista
  e a única base de preço-alvo, queda e ranking. Total parcelado, quantidade de
  parcelas e valor da parcela serão campos nullable separados na observação
  histórica; nenhum deles será calculado a partir dos demais. Não haverá
  backfill inferido para dados existentes.
- **Apresentação aprovada:** quando todos os dados forem explícitos,
  `💰 À vista: ...` e `💳 Parcelado: Nx de ... — total ...`; ausência ou
  evidência parcial nunca produz cálculo ou valor fictício.
- **Separação:** TASK-077 trata vendedor/entrega em Amazon e Kabum; TASK-089
  trata modalidades de preço nas quatro fontes. São independentes, mas devem
  ser executadas sequencialmente por compartilharem providers, persistência e
  Telegram.
- **Próxima ação:** manter TASK-089 planejada e não iniciada até pedido
  explícito do usuário.

## DEC-067 — Generalizar a evidência de vendedor/entrega para Kabum

- **Data:** 2026-08-16
- **Classificação:** Implementar agora, como refinamento da TASK-077.
- **Decisão:** além de distinguir Amazon própria de parceiro, a TASK-077 deve
  comprovar, persistir e disponibilizar na apresentação a condição
  vendido/entregue pela própria Kabum. O filtro `kabum_product=true` já limita
  a busca, mas não substitui evidência real dos cards nem persistência
  histórica da classificação.
- **Guardrails:** investigar Amazon e Kabum ao vivo antes do código; não assumir
  que as duas lojas expõem os mesmos campos; não tratar ausência como vendedor
  oficial; não alterar ranking; não ativar `Seller`/`Offer.seller_id`; revisar o
  enum proposto para não codificar `AMAZON` como conceito genérico.
- **Resultado do gate:** 48 cards Amazon e 24 cards Kabum foram inspecionados
  em buscas reais isoladas. Nenhum expôs vendedor ou responsável pela entrega;
  o seletor Amazon existente retornou `null` em todos.
- **Decisão posterior aprovada:** consultar somente páginas individuais dos
  candidatos finais, sequencialmente, sem retry e com limite três. Evidência
  real confirmou o bloco combinado `Enviado / Vendido` da Amazon com
  `Amazon.com.br` ou parceiro, e `Vendido e entregue por: KaBuM!` na KaBuM!.
  Persistir `seller_kind` e `fulfillment_kind` com enum genérico
  `platform`/`marketplace_partner`/`unknown`; `NULL` continua significando não
  avaliado. 401/403/429 interrompe o lote para não insistir contra anti-bot.

## DEC-066 — Mídia por oferta, redirect próprio e checkpoint por parte

- **Data:** 2026-08-16
- **Classificação:** Implementar agora, pela TASK-084 já planejada.
- **Decisão:** imagem nullable pertence a `Offer`; short link público e sem
  expiração mapeia token opaco único para `offer_id`; redirect valida esquema
  e compatibilidade com o host da loja; entrega Telegram mantém checkpoint por
  consumidor/evento/oferta/parte e retoma apenas partes ainda não confirmadas.
- **Evidência:** inspeção real isolada comprovou imagens nos cards das quatro
  lojas, com seletores e CDNs específicos registrados em `TASK-084.md`.
- **Guardrails:** nenhuma URL arbitrária no redirect; ausência de imagem não
  elimina oferta; falha de mídia cai para texto; ambiguidade não vira sucesso;
  nenhuma mudança em ranking, preço, frete ou classificação.


## DEC-065 — Listagem determinística de missões e menu Telegram sincronizado

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP.
- **Decisão:** criar a TASK-088 para adicionar `/listar_missoes`, restrito ao
  proprietário autenticado, sem IA, exibindo somente missões `active`,
  `paused` e `cancelled`; publicar o menu nativo pelo `setMyCommands` oficial.
- **Justificativa:** `/ajuda` já descreve os comandos atuais, mas o menu do
  Telegram não foi reaplicado depois do deploy. A consulta semântica por IA já
  lista missões, porém não substitui um comando explícito, previsível e barato.
- **Guardrails:** nenhuma transição, agenda, coleta, migration ou mudança de IA;
  `completed` e `expired` não entram na nova listagem.
- **Refinamento aprovado:** **Implementar agora** na própria TASK-088. A
  listagem agrupa `active` antes de `paused` e `cancelled`, mantendo as mais
  recentes primeiro em cada grupo, e apresenta os ícones oficiais
  `🟢 ativa`, `⏸️ pausada` e `❌ cancelada` junto ao status.


## DEC-064 — Revisão transversal de UX/copy como TASK própria

- **Data:** 2026-08-16
- **Classificação:** Nova TASK do MVP.
- **Decisão:** registrar a revisão aprovada dos textos visíveis como TASK-087,
  separada das TASKs funcionais, antes de alterar código.
- **Guardrail:** somente copy, layout textual e testes correspondentes; nenhum
  comando, parser, estado, TTL, autorização, regra funcional ou integração muda.
- **Resultado:** TASK-087 concluída com o catálogo aplicado, sem mudança de
  comportamento; suíte focada e não-integração aprovadas.

## DEC-063 — Diagnóstico local e seguro das falhas de coleta

- **Data:** 2026-08-16
- **Decisão:** enriquecer exclusivamente `collection_source_failed`, com
  traceback padrão limitado e mensagem somente para exceções de domínio
  consideradas seguras.
- **Motivo:** preservar diagnóstico sem ampliar o comportamento do formatter
  global nem expor texto bruto de bibliotecas externas.

## DEC-062 — Checks de Enum explícitos na metadata

- **Data:** 2026-08-16
- **Decisão:** representar `mission_command_values`,
  `store_source_type_values` e `user_role_values` como `CheckConstraint`
  explícitas e desativar a geração automática pelos respectivos `Enum`.
- **Motivo:** Alembic 1.19.1 ignora checks `_type_bound` na metadata, mas
  compara os mesmos checks refletidos do PostgreSQL, produzindo falso drift.
- **Compatibilidade:** nomes e expressões permanecem idênticos; nenhuma
  migration nem alteração no banco existente é necessária.

Este arquivo registra decisões arquiteturais e funcionais tomadas durante o desenvolvimento. Ele preserva o motivo de cada escolha e direciona a atualização documental necessária, sem substituir os ADRs para decisões arquiteturais formais.

## Processo obrigatório para novas funcionalidades

Antes de implementar qualquer funcionalidade sugerida, analisar seu impacto no escopo, nas dependências, na arquitetura, nos dados, na operação e na complexidade do projeto. A ideia deve receber exatamente uma das classificações abaixo:

- Implementar agora
- Nova TASK do MVP
- Backlog
- Versão futura
- Out of Scope
- Rejeitada

Após a classificação, registrar a decisão neste arquivo e atualizar a documentação correspondente. A classificação não autoriza implementação fora de uma TASK explicitamente solicitada.

## Formato de registro

### DEC-AAA — Título da decisão

- **Data:** AAAA-MM-DD
- **Ideia:** descrição objetiva da proposta ou decisão.
- **Classificação:** uma das seis classificações permitidas.
- **Justificativa:** impacto avaliado e motivo da classificação.
- **Próxima ação:** documento a atualizar, TASK a criar quando aplicável, ou ação de não implementação.

### DEC-060 — Ampliar o escopo da `v1.0.2` com dois itens depois da TASK-069

- **Data:** 2026-08-10
- **Ideia:** depois de aprovar a publicação da TASK-069 (que concluiu os 5
  itens do planejamento original da `v1.0.2`), o usuário pediu para
  registrar mais dois itens na mesma versão, antes do push: (1) impedir
  `/cadastro` para um usuário já autenticado/logado; (2) quando uma
  missão for criada sem nenhuma loja informada, perguntar as lojas por
  lista numerada (`1 Pichau`, `2 Terabyte`, `3 Amazon`, `4 Kabum`,
  `5 Todas`). Instrução explícita: não implementar agora, não abrir TASK
  automaticamente — só garantir que a documentação não afirme a `v1.0.2`
  inteira como concluída.
- **Classificação:** Nova TASK do MVP (dois itens registrados, escopo
  aberto — mesma classificação usada para os itens 3/4/5 adicionados por
  `DEC-057`/`DEC-055`/`DEC-058`).
- **Justificativa técnica:** mesma decisão de organização já usada nesta
  versão — o usuário prefere registrar pedidos pontuais na `v1.0.2` (release
  corretiva já em andamento) a abrir um novo documento de versão só para
  dois itens. Nenhum dos dois é infraestrutura/configuração pura, mas
  ambos já têm precedente estrutural direto: `/cadastro` já sabe detectar
  sessão ativa (`has_active_session`, TASK-046/061) e a lista numerada de
  lojas já existe em `favorite_stores`/`preferred_categories`
  (TASK-067) — nenhum dos dois exige mecanismo novo do zero.
- **Próxima ação:** itens 6 e 7 registrados em `docs/internal/v1.0.2-scope.md`; status da
  versão corrigido em todos os documentos que a citavam como "concluída"
  (`docs/internal/v1.0.2-scope.md`, `docs/internal/roadmap.md`, `docs/tasks/README.md`,
  `docs/internal/project-context.md`, `AGENTS.md`, `docs/releases/changelog.md`,
  `docs/tasks/TASK-069.md`) para deixar claro que só o planejamento
  *original* de 5 itens está concluído — a `v1.0.2` continua aberta.
  Nenhuma TASK criada para os dois itens novos; nenhuma implementação
  realizada.

### DEC-059 — Separar `v1.0.2` de V1.2 em documentos distintos

- **Data:** 2026-08-10
- **Ideia:** o usuário notou que `docs/internal/v1.2-scope.md` continha a seção da
  `v1.0.2` dentro de um arquivo cujo título e propósito declarado são só
  sobre V1.2 — mistura estrutural entre duas versões distintas (`v1.0.2`
  é release *patch* dentro da V1; V1.2 é fase funcional maior, numerada à
  parte), mesmo com as seções fisicamente separadas dentro do arquivo.
- **Classificação:** Implementar agora (correção de organização
  documental, sem mudança de conteúdo/decisão nenhuma — só o arquivo onde
  cada uma vive).
- **Justificativa técnica:** manter as duas em arquivos separados evita
  confundir as versões e deixa cada documento com um título e propósito
  únicos, sem exigir leitura de seções internas para saber a qual versão
  um item pertence.
- **Próxima ação:** conteúdo da `v1.0.2` movido para `docs/internal/v1.0.2-scope.md`
  (novo arquivo); `docs/internal/v1.2-scope.md` passa a conter só a V1.2 de verdade.
  Todas as referências cruzadas em `AGENTS.md`, `docs/internal/roadmap.md`,
  `docs/releases/changelog.md`, `docs/tasks/README.md`, `docs/internal/handoff-v1.0.2.md` e
  nas próprias entradas deste log (`DEC-052`, `DEC-055`, `DEC-057`)
  atualizadas para apontar para o arquivo certo.

### DEC-058 — Registrar pré-lista de preços sem IA como item 5 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário decidiu dividir a ideia original de "pré-lista de
  preços encontrados" (levantada durante a discussão do item de menor
  preço histórico) em duas fases: uma primeira versão simples, sem IA,
  mostrando só 1 preço por loja selecionada, na `v1.0.2`; e a versão com
  IA (julgamento de "vale a pena", comparação com histórico
  externo/interno) permanece só na V1.2, como já estava registrado no
  item 11 (`DEC-056`) — nada do que já tinha sido combinado para a V1.2
  foi removido ou reduzido, só ganhou uma fase anterior mais simples.
- **Classificação:** Versão futura (item 5 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Mesma nota
  dos itens 3 e 4 dessa release: é funcionalidade nova, não infra,
  registrada em `v1.0.2` por decisão explícita do usuário.
- **Justificativa técnica:** hoje o usuário só recebe alerta quando o
  preço cai ou atinge o alvo (`app/alerts/evaluator.py`) — nenhuma
  mensagem confirma que a missão está rodando nem mostra o que já foi
  encontrado. A versão sem IA é puramente informativa (apresenta dado já
  coletado, sem julgamento), preparando o terreno pra fase com IA da V1.2
  sem depender dela. Gatilho exato, formato da mensagem e template não
  decididos agora — ficam para a TASK.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 5; item 11 de
  `docs/internal/v1.2-scope.md` (`DEC-056`) atualizado só para referenciar essa fase
  anterior, sem alterar seu próprio conteúdo. Nenhuma TASK criada;
  implementação aguarda solicitação explícita futura.

### DEC-057 — Registrar edição de missão existente como item 3 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário perguntou se dá para editar uma missão já criada
  (trocar lojas ou preço-alvo sem recriar). Auditoria confirmou em
  `backend/app/missions/models.py`/`service.py`: `MissionCommand` só cobre
  transições de ciclo de vida (`activate`/`pause`/`resume`/`complete`/
  `cancel`/`expire`); não existe nenhum comando para alterar
  `MissionCriteria.target_amount`/`target_currency` nem as fontes
  selecionadas (`MissionSource`) — hoje só criando uma missão nova. O
  usuário pediu para registrar essa capacidade especificamente na
  `v1.0.2`, não na V1.2. Posicionada como item 3 (antes do item de
  categorias, `DEC-055`), por pedido explícito do usuário.
- **Classificação:** Versão futura (item 3 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Registrada
  em `v1.0.2` por decisão explícita do usuário, embora seja
  funcionalidade nova de verdade — mais distante do escopo original de
  "configuração/infraestrutura, sem funcionalidade nova" da `v1.0.2`
  (`DEC-052`) do que qualquer item anterior dessa mesma release (inclusive
  o item de categorias, `DEC-055`, que já era só um ajuste de UX).
  Diferença sinalizada explicitamente no `docs/internal/v1.0.2-scope.md` para não
  confundir escopo nem apagar o registro da decisão original.
- **Justificativa técnica:** o mecanismo exato (novo `MissionCommand`,
  fluxo de confirmação no Telegram, efeito sobre `mission_schedules` e
  sobre o histórico já coletado ao trocar fontes) não foi decidido —
  fica para quando a TASK for desenhada.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 3 da `v1.0.2`
  (arquivo separado de `docs/internal/v1.2-scope.md`, para não confundir as duas
  versões). Nenhuma TASK criada; implementação aguarda solicitação
  explícita futura.

### DEC-056 — Registrar comparação de menor preço histórico externo (itens 11 e 12 da V1.2)

> **Atualização (`DEC-080`/`DEC-082`, 2026-08-22):** o histórico externo
> permanece na V1.2, agora como item 17. A pesquisa de ofertas em lives foi
> movida para a V2. O texto abaixo preserva o contexto histórico original.

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu inicialmente um "pré-aviso" de valores
  encontrados e perguntou se a IA já analisa preços comparando com
  histórico — resposta confirmada por auditoria de código
  (`backend/app/alerts/evaluator.py`, `backend/app/purchase/`): nenhum dos
  dois módulos importa `AIProviderManager`; a decisão de alerta hoje é
  100% determinística (queda vs. observação anterior; alvo definido pelo
  usuário). A partir dessa resposta, o usuário ampliou o pedido para uma
  capacidade nos moldes do Steam Inventory Helper: mostrar preço atual,
  menor preço histórico **externo** (independente do ano, com fonte/URL/
  data verificáveis), menor preço histórico **interno** (já existe em
  `price_observations`), e comparação percentual entre os três, com regra
  explícita e sem exceção de que a IA nunca inventa preço/data/loja/fonte/
  URL — só interpreta fatos já encontrados por pesquisa externa real ou
  pelo próprio banco. Pediu também validação de identidade de produto
  antes de comparar (mesmo exemplo já validado em produção pela TASK-063:
  "Logitech G PRO 2" ≠ "Logitech G Pro X Superlight 2") e um template de
  apresentação fixo (fornecido por ele). Separadamente, pediu para
  registrar também pesquisa de ofertas anunciadas em lives — por enquanto
  só YouTube e Shopee Live —, ainda sem nenhum registro anterior no
  projeto.
- **Classificação:** Versão futura (itens 11 e 12 da V1.2, `docs/internal/v1.2-scope.md`;
  nenhuma TASK criada, nenhuma API/motor de busca escolhido, nenhuma
  implementação autorizada agora).
- **Justificativa técnica:** a regra "IA nunca inventa dado factual, só
  interpreta" já é o princípio em produção desde a TASK-063
  (`app.collection.relevance`) e o padrão de template fixo no código já é
  usado por `app/telegram/formatting.py` — o item 11 estende os dois
  padrões já aprovados para uma nova capacidade (pesquisa externa de
  histórico de preço) em vez de introduzir uma exceção a eles. A validação
  de identidade de produto reaproveita o mesmo tipo de verificação já
  validado em produção pela TASK-063, não um mecanismo novo. Pesquisa
  externa exige uma ferramenta/capacidade nova de busca (a API/motor fica
  para quando a TASK for desenhada) e deve continuar passando pela porta
  única de IA (`AIProviderManager`, `CLAUDE.md`) ou por uma ferramenta
  dedicada a desenhar então. O item 12 (lives) é uma fonte de dado
  estruturalmente diferente das páginas estáticas dos Store Providers
  atuais (Playwright sobre HTML) e foi mantido como item separado, sem
  detalhamento, por não ter escopo definido ainda.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com os itens 11 e 12. Nenhuma
  TASK criada; implementação, escolha de API/motor de busca e desenho da
  separação arquitetural (coleta / histórico interno / pesquisa externa /
  validação de identidade / dados estruturados / interpretação por IA /
  template final) ficam para quando a TASK for solicitada explicitamente.

### DEC-055 — Registrar lista numerada de categorias no `/cadastro` como item 4 da `v1.0.2`

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu para trocar o texto livre de "quais categorias
  você compra" no `/cadastro` por uma lista numerada das categorias
  conhecidas dos sites, permitindo resposta por números (ex.: `1,2,7,8,11`),
  no mesmo padrão já usado pelo passo de lojas favoritas
  (`favorite_stores`). Auditoria confirmou em
  `backend/app/users/registration.py`: `preferred_categories` hoje é texto
  livre parseado por `_parse_categories`, sem lista fechada;
  `favorite_stores`, passo anterior no mesmo fluxo, já usa exatamente o
  padrão pedido (`1 Kabum`/`2 Pichau`/`3 Terabyte`/`4 Amazon`/`5 Todas`,
  resposta por números separados por vírgula). Posicionada como item 4
  (depois do item de edição de missão, `DEC-057`), por pedido explícito do
  usuário.
- **Classificação:** Versão futura (item 4 da `v1.0.2`, `docs/internal/v1.0.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora). Registrado
  em `v1.0.2` por pedido explícito do usuário, embora seja um ajuste de
  UX/comportamento do cadastro, não de configuração/infraestrutura pura
  como os itens 1 e 2 dessa mesma release — diferença sinalizada no próprio
  `docs/internal/v1.0.2-scope.md` para não confundir o escopo original da `v1.0.2`
  (DEC-052).
- **Justificativa técnica:** o padrão já existe e já é usado com sucesso no
  passo imediatamente anterior do mesmo fluxo (`favorite_stores`) — replicar
  para categorias é consistência de UX, não uma capacidade nova. A lista
  real de categorias por loja (Kabum/Pichau/Terabyte/Amazon) precisa de
  levantamento próprio contra os quatro sites e fica para quando a TASK for
  criada, não decidida agora.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` atualizado com o item 4 da `v1.0.2`
  (arquivo separado de `docs/internal/v1.2-scope.md`, para não confundir as duas
  versões). Nenhuma TASK criada; implementação aguarda solicitação
  explícita futura.

### DEC-054 — Registrar Magalu como quinta fonte de oferta, item 10 da V1.2

> **Atualização (`DEC-080`/`DEC-082`, 2026-08-22):** o item de fontes da V1.2
> foi ampliado para Magalu, Mercado Livre e Shopee e renumerado como item 16.
> AliExpress permanece futuro e fora dessa etapa.

- **Data:** 2026-08-10
- **Ideia:** o usuário pediu para registrar a Magazine Luiza (Magalu) como
  nova loja pesquisável na V1.2, além das quatro já selecionáveis na V1
  (Pichau, Terabyte, Amazon, Kabum). Auditoria confirmou que a Magalu não
  era mencionada em nenhum documento do projeto até agora — sem conflito
  com o texto fixo da V1 (`CLAUDE.md`/`docs/internal/project-context.md`), que só
  cita Mercado Livre/Shopee/AliExpress como "Futuro" apresentado pelo bot;
  a Magalu é uma inclusão nova, independente dessas três.
- **Classificação:** Versão futura (item 10 da V1.2, `docs/internal/v1.2-scope.md`; nenhuma
  TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** mesma arquitetura de Store Provider já
  aprovada e usada pelas quatro fontes existentes (Playwright, normalização
  de preço/disponibilidade, integração com o filtro de relevância da
  TASK-063) — não introduz mecanismo novo, só mais uma fonte selecionável.
  Pesquisa de seletores/estrutura real do site, eventual tratamento
  anti-bot e ajuste da lista numerada de lojas do `/cadastro` ficam para a
  TASK, quando solicitada.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com o item 10. Nenhuma TASK
  criada; implementação aguarda solicitação explícita futura.

### DEC-053 — Registrar redução de `PriceObservation` redundante como item 9 da V1.2

- **Data:** 2026-08-10
- **Ideia:** com a `v1.0.1` em produção real (coleta a cada 30 min por
  missão ativa, `DEC-046`), o usuário observou que o volume de
  `price_observations` vai crescer proporcional à frequência de polling,
  não à frequência real de mudança de preço — cada coleta grava uma linha
  nova mesmo quando o estado da oferta não mudou. Propôs uma regra para
  evitar observações redundantes da mesma oferta, cobrindo no mínimo preço
  e disponibilidade, pedindo antes uma auditoria dos campos reais de
  `PriceObservation` para decidir a comparação com precisão. Auditoria
  feita (`backend/app/collection/models.py`): campos comparáveis são
  `amount`, `currency`, `shipping_amount`, `availability` e `fulfillment`;
  `total_amount` é derivado, `observed_at`/`recorded_at` são timestamps,
  `raw_evidence` é evidência bruta e não deve gatear a comparação. Decisão
  final do usuário: só criar `PriceObservation` nova quando pelo menos um
  campo comparável mudar (usando `get_latest_price_observation`, já
  existente); coleta com o mesmo estado não grava linha nova, mas a oferta
  precisa continuar registrando que foi vista de novo sem gerar observação
  redundante — forma exata (`last_seen_at` ou equivalente) fica para a
  TASK. Histórico já gravado nunca é apagado nem compactado por este item.
  Compactação/arquivamento de histórico antigo já existente fica só como
  ideia futura registrada, sem decisão de implementação — exigiria abrir
  exceção explícita à regra de preservação de histórico do `CLAUDE.md`,
  decisão própria e separada desta.
- **Classificação:** Versão futura (item 9 da V1.2, `docs/internal/v1.2-scope.md`;
  nenhuma TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** o histórico deve representar mudanças reais de
  estado da oferta, não a cadência de polling do `collection_worker`; a
  regra não altera o desenho append-only de `PriceObservation` (nenhuma
  linha existente é apagada ou reescrita, só deixa de criar linhas
  redundantes daqui pra frente) e não conflita com a preservação de
  histórico do `CLAUDE.md`, já que nada gravado é descartado. A ideia de
  compactar observações antigas já existentes é uma exceção real a essa
  regra e foi deliberadamente separada, para não comprometer a decisão mais
  simples e não controversa (parar de gravar duplicata) com uma decisão
  mais sensível que ainda não tem justificativa de necessidade real.
- **Próxima ação:** `docs/internal/v1.2-scope.md` atualizado com o item 9, ordem de
  execução após os itens já existentes. Nenhuma TASK criada; implementação
  aguarda solicitação explícita futura.

### DEC-052 — Registrar `v1.0.2` como release corretiva de configuração/infraestrutura, antes da V1.2

- **Data:** 2026-08-10
- **Ideia:** a preparação de `docs/installation/linux-legacy-setup.md` para a `v1.0.1`
  encontrou dois ajustes corretivos válidos, nenhum bloqueador da
  `v1.0.1`: (1) `AISHOPPING_GEMINI_MODEL`/`AISHOPPING_GROQ_MODEL` existem em
  `.env`/`.env.example`/`backend/.env.example` mas não são propagadas pelo
  `compose.yaml` atual, sem efeito real em produção; (2) só
  `collection_worker` e `telegram_notifier` têm `restart: unless-stopped`
  em `compose.yaml` — os outros cinco serviços não voltam sozinhos após
  reboot/queda de energia/crash. O usuário decidiu não implementar nenhum
  dos dois agora (prioridade é colocar a `v1.0.1` em produção primeiro) e
  registrar formalmente uma futura release corretiva **`v1.0.2`** —
  sem funcionalidade nova, só configuração/infraestrutura — que deve
  acontecer depois da `v1.0.1` estar em produção e observada, e **antes**
  da V1.2 funcional já listada em `docs/internal/v1.2-scope.md`.
- **Classificação:** Versão futura (`v1.0.2`, inicialmente registrada
  dentro de `docs/internal/v1.2-scope.md`; posteriormente movida para o documento próprio
  `docs/internal/v1.0.2-scope.md` para não misturar as duas versões no mesmo arquivo —
  nenhuma TASK criada, nenhuma implementação autorizada agora).
- **Justificativa técnica:** os dois achados são reais (confirmados por
  auditoria de `compose.yaml` durante a TASK-064/preparação do manual de
  produção) mas de baixo risco e não funcionais — remoção de configuração
  morta e ajuste de resiliência operacional, não mudança de comportamento
  de IA nem de arquitetura. Adiar para uma release corretiva dedicada
  (`v1.0.2`) evita misturar correção de infraestrutura com a primeira
  subida real em produção da `v1.0.1`, e evita competir com o escopo
  funcional já priorizado da V1.2. A cascata `Gemini Flash → Groq`
  (`DEC-050`) não é alterada por nenhum dos dois itens.
- **Próxima ação:** `docs/internal/v1.0.2-scope.md` (documento próprio, separado de
  `docs/internal/v1.2-scope.md`) registra os itens com ordem de execução (`v1.0.2` antes
  da V1.2). `docs/installation/linux-legacy-setup.md` atualizado para deixar explícito,
  no comportamento real da `v1.0.1`, que ambos os pontos estão planejados
  para correção na `v1.0.2`. Nenhuma TASK criada
  ainda; implementação aguarda solicitação explícita futura, depois da
  `v1.0.1` estar em produção.

### DEC-051 — Manter `v1.0.0` imutável; publicar `v1.0.1` como release corretiva atual

- **Data:** 2026-08-10
- **Ideia:** auditoria (a pedido do usuário) confirmou que a tag `v1.0.0`
  (`85b56c6`, criada pela TASK-054 em 2026-08-09) nunca foi movida e não
  contém as correções da TASK-063 (`DEC-048`, relevância/apresentação dos
  alertas) nem da TASK-064 (`DEC-049`/`DEC-050`, cascata Gemini Flash →
  Groq), ambas concluídas e aprovadas depois da tag existir. O
  `docs/releases/checklist.md` marcado "65/65" refletia o código corrente
  (`main`/branch da TASK-064), não o conteúdo efetivamente publicado sob
  `v1.0.0` — uma divergência real entre "release definitiva" e "estado
  aprovado atual". Decisão do usuário: `v1.0.0` **permanece intocada**,
  como marco histórico do estado da V1 em 2026-08-09 (não deve ser usada
  como referência de deploy); uma nova tag **`v1.0.1`** é publicada sobre
  o commit atual (que inclui TASK-063 e TASK-064) como a release corretiva
  e referência corrente.
- **Classificação:** Implementar agora (ação administrativa de
  versionamento sobre escopo já aprovado — TASK-054/TASK-063/TASK-064 —,
  sem nenhuma mudança de código ou arquitetura).
- **Justificativa técnica:** semver de correção (`v1.0.0` → `v1.0.1`) é
  apropriado porque TASK-063 e TASK-064 são correções sobre o mesmo escopo
  do MVP da V1 (`docs/internal/mvp.md`), não funcionalidades novas. Manter `v1.0.0`
  imutável preserva o histórico auditável e evita reescrever uma tag já
  publicada em `origin`; publicar uma tag nova em vez de mover a existente
  é a forma correta de corrigir a divergência sem apagar evidência do
  estado anterior.
- **Próxima ação:** `docs/releases/checklist.md`, `docs/tasks/TASK-054.md`
  e `docs/releases/changelog.md` atualizados nesta mesma revisão. Publicação da tag
  `v1.0.1` e atualização de `origin/main` aguardam autorização final
  explícita do usuário, em separado desta decisão.

### DEC-050 — Eliminar o nível Gemini Pro/preview da V1; USER/ADMIN/DEV usam só Flash

- **Data:** 2026-08-09
- **Ideia:** depois da auditoria da TASK-064 (`DEC-049`) mostrar que tanto o
  modelo premium configurado (`gemini-3.1-pro-preview`, preview) quanto um
  candidato GA "Pro" (`gemini-pro-latest`) falham com a chave atual
  (`quota_exceeded` imediato no candidato GA), o usuário rejeitou
  explicitamente continuar procurando um modelo Gemini Pro/premium
  alternativo. Decisão final: `USER`, `ADMIN` e `DEV` usam o mesmo modelo
  Gemini Flash (o gratuito já configurado, `Settings.gemini_model`) para
  operações automáticas de IA; a distinção de papel continua sendo só de
  permissão/autorização, nunca de modelo. Fallback só por disponibilidade:
  Gemini Flash → Groq → outros já aprovados, nunca dois modelos Gemini
  equivalentes em sequência.
- **Classificação:** Implementar agora (dentro do escopo já aprovado da
  TASK-064; não é ampliação — é simplificação de infraestrutura de IA já
  existente, TASK-059/`DEC-016`).
- **Justificativa técnica:** `AdminDevAIProviderManager` hoje monta 3
  camadas (premium, Groq opcional, gratuito), sendo as camadas 1 e 3 sobre
  a mesma chave `gemini_api_key_admin_dev`. Removendo a camada "Pro", elas
  ficam idênticas — a simplificação correta é colapsá-las numa cascata de
  2 camadas (`gemini_model` → Groq), eliminando
  `gemini_premium_model`/`AISHOPPING_GEMINI_PREMIUM_MODEL` do config e a
  tentativa redundante contra o mesmo modelo duas vezes. Afeta tanto as
  chamadas automáticas da TASK-063 (`collection_worker`) quanto as
  chamadas interativas de ADMIN/DEV via Telegram, que compartilham o mesmo
  `AdminDevAIProviderManager` — ambas se beneficiam de não gastar uma
  tentativa garantidamente perdida. `UserAIProviderManager` não muda (já
  usa só `gemini_model`, sem fallback). Memória de projeto registrada:
  `project_gemini_flash_only_v1.md`.
- **Próxima ação:** `docs/tasks/TASK-064.md` atualizado com o plano de
  implementação decorrente (cascata de 2 camadas) e os testes/validação
  necessários. Implementação aguarda autorização explícita do usuário.
  TASK-054/`v1.0.0` continua suspensa até a TASK-064 fechar.

### DEC-049 — Criar a TASK-064 para revisar disponibilidade/fallback dos provedores de IA

- **Data:** 2026-08-09
- **Ideia:** a validação real da TASK-063 (`DEC-048`) revelou um problema
  separado: `gemini-3.1-pro-preview` (camada premium do
  `AdminDevAIProviderManager`) teve 0 sucessos em 248 tentativas reais. O
  usuário pediu para tratar isso como TASK própria — auditar a cascata
  ADMIN/DEV, validar com chamadas mínimas quais modelos a chave atual
  realmente consegue usar, propor (sem implementar ainda) a melhor ordem
  de fallback, e só considerar batching depois, com evidência real.
- **Classificação:** Nova TASK do MVP (a cascata ADMIN/DEV já é
  infraestrutura aprovada da V1, TASK-059/DEC-016; esta TASK corrige sua
  disponibilidade prática, não amplia escopo — nenhum provedor novo, nenhum
  canal novo).
- **Justificativa técnica:** auditoria (`docs/tasks/TASK-064.md`) confirmou
  que `gemini-3.1-pro-preview` é oficialmente `preview` (`stable=False` na
  própria listagem da API). Um teste mínimo (uma chamada de
  `client.models.list()` mais duas chamadas reais de `generateContent`,
  sem carga adicional) mostrou que um candidato GA "Pro" (`gemini-pro-latest`)
  também falha com `quota_exceeded` de imediato, enquanto um modelo GA
  "Flash" (`gemini-3.5-flash`) responde normalmente — mais consistente com
  a chave não ter cota real de nível "Pro" do que com um problema
  específico do modelo preview escolhido. A taxonomia de erro
  (quota/indisponibilidade/timeout/autenticação/rejeição) já está separada
  corretamente no código (`_translate_api_error` idêntica em
  `gemini.py`/`groq.py`); o único gap de observabilidade encontrado é
  cosmético (falha de parsing de resposta não se correlaciona automaticamente
  com a tentativa de provedor bem-sucedida que a originou). Volume real por
  coleta é 2 operações lógicas de IA por oferta nova (nunca recorrente —
  cache permanente por `(mission_id, offer_id)`/`Product`); o pico de 248
  chamadas visto na validação veio de várias missões ficando due ao mesmo
  tempo após um restart do worker, não de uma única coleta.
- **Próxima ação:** `docs/tasks/TASK-064.md` criado com a auditoria e duas
  propostas de nova cascata (Opção A: dois modelos Flash estáveis, sem
  depender de "Pro"; Opção B: manter uma camada "Pro" GA, se o usuário
  confirmar faturamento habilitado na chave). Implementação aguarda
  autorização explícita, incluindo a resposta à pergunta sobre faturamento.
  TASK-054/`v1.0.0` permanece suspensa até esta TASK fechar.

### DEC-048 — Criar a TASK-063 para relevância de resultados e apresentação de alertas

- **Data:** 2026-08-09
- **Ideia:** antes de tratar a V1 como definitivamente pronta, o usuário
  identificou no Telegram real que alertas de preço podiam corresponder a
  itens irrelevantes (acessórios, modelos errados) e sempre mostravam o
  nome da missão em vez do nome real do anúncio, sem link direto visível.
  Pediu auditoria completa do fluxo `StoreProvider → Product/Offer →
  PriceObservation → evaluator → evento → telegram_notifier`, uso do
  `AIProviderManager` já existente para normalizar título e classificar
  correspondência (`MATCH`/`POSSIBLE_MATCH`/`NO_MATCH`), revisão do
  template de alerta e testes cobrindo os casos críticos — sem implementar
  antes de autorização explícita.
- **Classificação:** Nova TASK do MVP (corrige rastreabilidade exigida pelo
  critério 4 de `docs/internal/mvp.md`: "uma condição de preço... produz evento e
  notificação rastreáveis" — hoje a notificação existe, mas não é
  rastreável ao anúncio real, e pode não corresponder ao produto pedido).
  Não é ampliação de escopo: usa o `AIProviderManager` já aprovado, sem
  novo provedor de IA, novo canal ou nova loja.
- **Justificativa técnica:** auditoria (`docs/tasks/TASK-063.md`) confirmou
  causa raiz concreta e não hipotética: `Offer.url` já é a URL real e
  correta; `Product.name` guarda o título bruto só na primeira coleta da
  oferta, nunca normalizado; `telegram/notifications.py::_render_alert` usa
  só `mission.title`, nunca busca `Offer`/`Product`/`Store` a partir do
  `offer_id` já presente nos payloads de evento; e
  `evaluate_price_alerts` roda para todo item devolvido pela busca do
  site, sem nenhum filtro de correspondência produto-missão. Um achado
  adicional (busca de `previous` observação sem filtrar por missão,
  compartilhando estado de "alvo já atingido" entre missões diferentes na
  mesma oferta) foi registrado na auditoria, mas fica fora do escopo desta
  TASK até decisão explícita do usuário.
- **Próxima ação:** `docs/tasks/TASK-063.md` criado com auditoria,
  diagnóstico e plano proposto; implementação aguarda autorização explícita
  do usuário, incluindo a regra para `POSSIBLE_MATCH`. A tag `v1.0.0`
  (TASK-054) permanece publicada sem alteração, mas deixa de ser tratada
  como estado final da V1 até a TASK-063 fechar (ver notas em
  `docs/tasks/TASK-054.md` e `docs/releases/checklist.md`).

### DEC-047 — Backoff persistente por `(mission_id, source)` em `MissionSource`, não na `MissionSchedule`

- **Data:** 2026-08-09
- **Ideia:** aprovar e implementar a modelagem mínima de backoff persistente
  proposta em `DEC-046`, corrigida por duas restrições explícitas do
  usuário: (1) o backoff não pode atrasar a missão inteira quando só uma
  das quatro lojas selecionadas está bloqueada — cada bloqueio confirmado
  deve afetar somente aquela fonte específica; (2) o gatilho não pode
  incluir 401 — só 403, 429 e challenge/CAPTCHA/proteção externa
  confirmada contam, porque 401 normalmente representa
  autenticação/credencial/configuração, não proteção anti-bot, e não deve
  crescer exponencialmente como se fosse rate limit (a chamada ainda falha
  normalmente; só o backoff persistente fica de fora). `MissionSource`
  (já a entidade `(mission_id, store_id)`) ganhou `next_eligible_at` e
  `consecutive_blocks` (migração `20260809_0003`); `claim_due_collections`
  passou a filtrar por fonte, sem tocar em `MissionSchedule.next_run_at`.
  O gatilho ficou restrito a bloqueio externo **confirmado** (status
  403/429 do próprio `ProviderBlockedError`) — não dispara para 401,
  timeout, erro de rede, erro de parsing, erro interno, nem para o mesmo
  `ProviderBlockedError` com status ambíguo (seletor ausente/oferta vazia,
  possível mudança de markup). A fórmula (`2**consecutive_blocks`,
  teto 6h) usa sempre o `interval_minutes` já configurado da missão, nunca
  um valor fixo, e o contador para de crescer assim que o teto é atingido.
  Sucesso reseta só a fonte que teve sucesso.
- **Classificação:** Implementar agora.
- **Justificativa técnica:** `MissionSource` já existia com a granularidade
  certa (PK composta `mission_id`/`store_id`), reutilizada pelo próprio
  `claim_due_collections` para decidir quais fontes reivindicar — bastou
  adicionar 2 colunas e um filtro a mais na mesma consulta, sem tabela nova
  nem mudança na cadência da missão. `advance_schedule`/`MissionSchedule`
  continuam exatamente como antes: o gate existente
  `if mission_claims: advance_schedule(...)` já garante que uma missão com
  todas as fontes em backoff permanece due (reexaminada no próximo poll,
  não no próximo intervalo inteiro) sem precisar de nenhuma mudança de
  arquitetura — confirmado com teste de integração real
  (`test_all_sources_in_backoff_creates_no_run_and_schedule_stays_due`).
  Distinguir bloqueio confirmado (403/429) de `ProviderBlockedError`
  ambíguo (mesma exceção, status diferente, já usada hoje também para
  seletor ausente/oferta vazia, e para 401) evita que um possível bug de
  mudança de markup na loja — ou uma falha de autenticação/configuração —
  seja tratado como se fosse proteção anti-bot confirmada. A chamada em si
  continua tratada como bloqueio pelo `app.collection.providers.base`
  existente (401/403/429 seguem interrompendo o fallback daquele ciclo);
  só o backoff persistente por fonte ficou mais restrito.
- **Próxima ação:** `docs/architecture/mission-schedules.md` atualizado com a modelagem
  final. Testes unitários (função pura de backoff, classificação de erro,
  wiring de `_record_failure`/`_persist_success`) e de integração real
  (ciclo completo de bloqueio → exclusão do claim → expiração → segundo
  bloqueio; todas as fontes bloqueadas não cria run nem erro) aprovados via
  `scripts/check.ps1` (723 testes rápidos, 90,43% de cobertura, 13
  integrações PostgreSQL, migração `20260809_0003`). TASK-053 continua sem
  fechamento até a validação E2E final ser refeita com este estado.

### DEC-046 — Intervalo de coleta configurável com stagger; 30 min é implantação temporária de 8 GB, alvo da V1 é 15 min

- **Data:** 2026-08-09
- **Ideia:** durante a auditoria de frequência de coleta pedida na retomada da
  TASK-053, ficou confirmado que a V1 já usa um intervalo fixo e global
  (`collection_schedule_interval_minutes`, `Settings`) para todas as
  missões, sem jitter/stagger e sem backoff por bloqueio externo no nível
  da agenda. O usuário decidiu, dado que o servidor de produção está
  temporariamente com 8 GB de RAM (upgrade para 16 GB previsto): (1) usar
  30 minutos como valor de implantação temporário, para reduzir o pico de
  Chromium/RAM; (2) usar `AISHOPPING_COLLECTION_MAX_CONCURRENCY=2` (em vez
  de 4) pelo mesmo motivo, sem remover a capacidade de voltar a 4; (3)
  manter 15 minutos como alvo pretendido da V1 assim que o servidor tiver
  16 GB; (4) adicionar stagger (deslocamento aleatório pequeno, só na
  criação/backfill/reativação da agenda, nunca recalculado em restart)
  para que missões com o mesmo intervalo não fiquem sincronizadas no
  mesmo instante; (5) **rejeitar** a primeira proposta de backoff após
  401/403/429 no nível da `MissionSchedule` inteira (uma loja bloqueada
  atrasaria a consulta das outras três da mesma missão) — o backoff
  persistente precisa ser por `(mission_id, source)`, com modelagem
  mínima a ser apresentada e aprovada antes de qualquer migration.
- **Classificação:** Implementar agora (intervalo temporário de 30 min,
  `max_concurrency=2` temporário, stagger na criação/backfill); Nova TASK
  do MVP ou complemento desta TASK, a definir (backoff persistente por
  fonte, modelagem ainda pendente de aprovação).
- **Justificativa técnica:** `MissionSchedule.interval_minutes` é uma
  coluna gravada por linha, lida por `advance_schedule` — nunca por
  leitura ao vivo de `Settings`. Trocar a env var de 30 para 15 no futuro
  **não migra agendas já persistidas**: só afeta missões criadas/
  recuperadas depois da troca. É preciso um `UPDATE` explícito (não uma
  migration Alembic — não há mudança de schema) no momento do upgrade
  para 16 GB, documentado como procedimento operacional em
  `docs/operations/linux-runbook.md`, para evitar coexistência silenciosa de missões em
  30 e 15 minutos. Também foi corrigida uma lacuna pré-existente: o
  serviço `api` (onde `/cadastro`/criação de missão via Telegram roda) não
  recebia `AISHOPPING_COLLECTION_SCHEDULE_INTERVAL_MINUTES` no
  `compose.yaml`, então missões novas criadas pelo Telegram usariam
  sempre o padrão de código (60 min) independentemente da env var
  configurada para o `collection_worker`; agora as duas fontes de criação
  de agenda (Telegram/`api` e backfill/`collection_worker`) leem a mesma
  env var. `collection_max_concurrency` é puramente configuração de
  runtime (não persistida): reduzir para 2 e voltar para 4 depois é
  seguro e reversível só reiniciando o `collection_worker`. O backoff por
  missão inteira foi rejeitado porque acopla a saúde de uma fonte à
  frequência de coleta das outras três, prejudicando diretamente a
  detecção rápida de promoções — objetivo central do produto.
- **Próxima ação:** `docs/architecture/mission-schedules.md` e `docs/operations/linux-runbook.md`
  atualizados com a distinção entre alvo da V1 (15 min) e implantação
  temporária de 8 GB (30 min), e o procedimento de upgrade. Modelagem
  mínima de backoff por `(mission_id, source)` a apresentar antes de
  qualquer migration; TASK-053 continua sem fechamento até essa peça e as
  demais pendências serem resolvidas.

### DEC-045 — Alertas da V1 monitoram `amount` (preço do produto), não `total_amount`; frete deixa de bloquear a TASK-053

- **Data:** 2026-08-09
- **Ideia:** a TASK-053 ficou `BLOCKED_EXTERNAL` porque as quatro lojas
  reais nunca revelam frete sem login no marketplace, e
  `evaluate_price_alerts` (TASK-027) rejeitava qualquer observação com
  `shipping_amount is None`. Decisão de produto do usuário: na V1,
  monitoramento/alertas de preço não exigem frete nem login em loja — só o
  preço do produto (`PriceObservation.amount`). Frete/parcelamento
  precisos ficam para depois: V1.2 (só ADMIN/DEV, com sessão autenticada
  nas lojas) e V2 (usuários comuns, com desenho de credenciais/sessões
  isoladas próprio).
- **Classificação:** Implementar agora (correção de escopo dos alertas e
  do critério de elegibilidade externa da TASK-053); Versão futura (itens
  de V1.2/V2 registrados abaixo).
- **Justificativa técnica:** a primeira proposta desta correção pretendia
  continuar comparando `total_amount` (que hoje é `amount +
  COALESCE(shipping_amount, 0)`). O usuário apontou que isso é errado:
  quando o frete muda de conhecido para desconhecido (ou vice-versa) entre
  duas observações da mesma oferta, `total_amount` mistura bases
  diferentes e pode gerar alerta falso. Exemplo concreto: observação
  anterior `amount=2000, shipping=100 → total=2100`; observação atual
  `amount=2050, shipping=None → total=2050`. Comparar `total_amount`
  diria "caiu" (2050 < 2100) quando o preço do produto na verdade **subiu**
  (2000 → 2050). Por isso a série de monitoramento da V1 compara sempre
  `amount` (produto vs. produto), nunca `total_amount` — em nenhuma das
  duas pontas da comparação (observação atual nem anterior), e a mesma
  base é usada tanto na decisão quanto no payload do evento publicado
  (`previous_total`/`current_total`/`target_total` recebem o valor de
  `amount`, não de `total_amount`, para os alertas da V1 — nomes de campo
  do catálogo não mudam, só a origem do valor). `total_amount` continua
  existindo, sendo persistido normalmente e sendo a base de custo final
  usada por `app.purchase` (recomendação/comparação/confirmação,
  TASK-038 a TASK-041), que **não muda** — frete desconhecido continua
  tornando uma oferta inelegível para afirmar custo total ali. A
  separação fica explícita: **alertas V1 = `amount`; custo
  final/compra = `amount + shipping` conhecido.** O critério de
  elegibilidade externa da TASK-053
  (`backend/scripts/validate_external_e2e.py`) deixa de exigir
  `shipping_amount is not None`, exigindo só disponibilidade válida e
  moeda compatível. Frete nunca é fabricado nem tratado como zero/grátis
  quando desconhecido — só deixou de ser exigido para o monitoramento de
  preço da V1.
- **V1.2 registrada** (`docs/internal/v1.2-scope.md`, item 4): "Consulta autenticada de
  frete e parcelamento para ADMIN/DEV" — só o proprietário do sistema
  inicialmente, usando sessão autenticada nas lojas (Pichau, Terabyte,
  Amazon, Kabum conforme suporte real) para obter frete/parcelamento reais,
  com carrinho apenas quando necessário e CEP configurado. Regras de
  segurança já registradas para quando a TASK for desenhada: credenciais
  nunca em prompt de IA/logs/traces/métricas/auditoria, sem senha em texto
  puro, sessão restrita ao provider, nenhuma conta compartilhada com
  `USER` comum, login/carrinho nunca autorizam compra sozinhos.
- **V2 registrada** (`docs/internal/backlog.md`): "Frete e parcelamento
  autenticados por usuário" — mesma capacidade da V1.2, aberta a usuários
  comuns, exigindo desenho próprio de credenciais/sessões isoladas por
  usuário, autorização e ciclo de vida de sessão.
- **Próxima ação:** `backend/app/alerts/evaluator.py` e
  `backend/scripts/validate_external_e2e.py` corrigidos; documentação
  sincronizada (`docs/architecture/price-alerts.md`, `docs/tasks/TASK-027.md`,
  `docs/architecture/mission-criteria.md`, `docs/development/e2e-tests.md`,
  `docs/tasks/TASK-053.md`, `docs/architecture/price-engine.md`); reexecutar a
  TASK-053 (pipeline, E2E reproduzível, E2E externo real) e atualizar seu
  resultado real, sem presumir sucesso antes de rodar. TASK-054 continua
  aguardando a conclusão real da TASK-053.

### DEC-042 — Corrigir onboarding descoberto pelo E2E

- **Data:** 2026-08-09
- **Ideia:** tornar a seleção de lojas do `/cadastro` numerada, emitir o link
  inicial de senha ao concluir o cadastro e reduzir o mínimo da senha para oito
  caracteres sem impor regras artificiais de composição.
- **Classificação:** Implementar agora.
- **Justificativa:** o E2E real da TASK-053 demonstrou que texto livre sem IA
  induzia o usuário a um formato não explicado e que separar `/senha` deixava o
  onboarding incompleto. O fluxo continua determinístico e não recebe senha no
  Telegram. O mínimo de oito é uma escolha de usabilidade da V1 protegida por
  Telegram privado, Argon2id, blocklist, limites e cooldown; não é apresentado
  como conformidade ou MFA formal, que continuam futuros. Verificação de
  e-mail permanece V2.
- **Próxima ação:** concluir as correções dentro da TASK-053 e repetir os dois
  modos E2E antes de fechar a tarefa.

**Atualização pontual (2026-08-15):** o mínimo permanece em oito caracteres,
mas a política final aprovada passou a exigir ao menos uma letra maiúscula, uma
letra minúscula, um número e um símbolo. Esta atualização substitui somente a
parte acima que dispensava composição; Argon2id, blocklist, limite máximo de
128 caracteres e demais proteções permanecem.

### DEC-043 — Confirmar operações de autenticação no chat

- **Data:** 2026-08-09
- **Ideia:** registrar no chat privado do Telegram as conclusões de criação,
  alteração e recuperação de senha e de login, além de avisar uma única vez
  antes da expiração e quando a sessão expirar.
- **Classificação:** Implementar agora.
- **Justificativa:** a senha continua restrita à página HTTPS, mas o retorno
  durável no mesmo chat em que a operação foi iniciada torna o estado de
  autenticação observável para a pessoa e cria um histórico operacional no
  Telegram. Eventos persistentes, consumo idempotente e marcadores atômicos
  por sessão evitam perda e duplicidade após restart, sem transformar
  preferências de alertas de preço em preferências de segurança.
- **Próxima ação:** implementar e validar dentro da TASK-053, incluindo
  PostgreSQL e Telegram reais.

### DEC-044 — Negociar orçamento ausente e sugerir referência na V1.2

- **Data:** 2026-08-09
- **Ideia:** quando o pedido de missão não trouxer valor, perguntar primeiro se
  a pessoa possui um orçamento; na ausência dele, consultar histórico e, se
  necessário, fontes externas para sugerir uma média de itens/marcas de menor
  preço antes da criação.
- **Classificação:** Versão futura.
- **Justificativa:** o fluxo exige contrato conversacional novo, critério de
  qualidade para amostra e marcas comparáveis, consulta externa adicional e
  regras para evidência insuficiente. O usuário reservou expressamente essa
  evolução à V1.2; introduzi-la durante o fechamento E2E da V1 ampliaria o MVP.
- **Próxima ação:** manter no backlog da V1.2 e criar especificação/TASK
  própria antes de implementar. A V1 não deve afirmar genericamente que usará
  “quatro lojas padrão” como substituto dessa conversa.

### DEC-040 — Isolar integração real por banco descartável

- **Data:** 2026-08-08
- **Ideia:** tornar os fluxos persistentes críticos uma suíte permanente contra
  PostgreSQL real sem permitir contato acidental com dados do operador.
- **Classificação:** Implementar agora.
- **Justificativa:** mocks e transações globais não exercitam migrations,
  constraints, commits ou corridas reais. Um PostgreSQL 18.4 fixado por digest,
  com recursos exclusivos por execução e banco clonado por teste, oferece
  isolamento determinístico. Guards de ambiente/banco, loopback e cleanup exato
  fazem a suíte falhar fechado sem usar prune ou infraestrutura real.
- **Próxima ação:** TASK-052 concluída; o preflight da TASK-053 originou a
  correção de ordem registrada posteriormente na DEC-041.

### DEC-041 — Orquestrar coletas antes dos testes E2E

- **Data:** 2026-08-09
- **Ideia:** criar a TASK-062 para ligar automaticamente agendas vencidas,
  fontes selecionadas, Store Providers, persistência de observações, avaliação
  e publicação no event log antes de executar a TASK-053.
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** o código possui todos esses componentes isolados, mas
  `create_mission_from_criteria` não cria agenda e nenhum processo consome
  `mission_schedules`. Um E2E que chamasse os componentes manualmente provaria
  apenas o test harness, não o funcionamento real da V1. A nova TASK fecha um
  requisito já existente nos critérios 3 e 4 do MVP sem adicionar produto,
  loja, IA ou infraestrutura distribuída.
- **Próxima ação:** TASK-062 concluída e documentada em
  `docs/tasks/TASK-062.md`; executar a TASK-053 sobre a cadeia real, sem início
  automático.

### DEC-039 — Operar de forma privada com recuperação manual comprovada

- **Data:** 2026-08-08
- **Ideia:** consolidar o runbook do Ubuntu Server, tornar binds administrativos
  privados por padrão e validar backup/restauração sem prometer disaster
  recovery ou rollback universal.
- **Classificação:** Implementar agora.
- **Justificativa:** documentação operacional precisa reproduzir início,
  diagnóstico e recuperação básica sem expor PostgreSQL/telemetria. Backup só
  tem valor depois de restauração comprovada, enquanto versões anteriores da
  aplicação podem ser incompatíveis com o schema atual. Loopback por padrão,
  restauração em banco limpo e bloqueio de downgrade automático reduzem perda e
  exposição sem criar infraestrutura de V2.
- **Próxima ação:** concluir a TASK-051; manter scheduler, testes permanentes,
  release, domínio/TLS e disaster recovery completo fora desta tarefa.

### DEC-038 — Desidentificar sem reescrever históricos

- **Data:** 2026-08-08
- **Ideia:** remover identificadores diretos e limitar retenção operacional sem
  transformar UUID e fatos correlacionáveis em falsa alegação de anonimização.
- **Classificação:** Implementar agora.
- **Justificativa:** conta, Telegram, textos livres e autenticação exigem
  minimização, mas preços, eventos, transições, auditoria e confirmações são
  fatos protegidos por imutabilidade/FKs. A operação transacional remove dados
  diretos e falha antes de mutar se detectar PII em histórico append-only.
  Telemetria recebe limites explícitos e não se afirma certificação LGPD.
- **Próxima ação:** TASK-050 concluída; executar a TASK-051. Anonimização
  irreversível de bases históricas exige avaliação futura específica.

### DEC-037 — Persistir replay/retry e manter resiliência externa local

- **Data:** 2026-08-08
- **Ideia:** impedir abuso, replay e cascatas de falha sem adicionar
  infraestrutura distribuída à V1.
- **Classificação:** Implementar agora.
- **Justificativa:** `update_id`, cota por usuário e histórico de consumo
  precisam sobreviver a restart e concorrência, portanto usam fatos append-only
  no PostgreSQL. Timeout, retry de operações seguras e circuit breaker podem
  permanecer locais por processo. `sendMessage` e outras operações
  potencialmente não idempotentes não recebem retry cego; eventos não ganham
  estado mutável. O desenho fecha a TASK-049 com segurança sem Redis ou broker.
- **Próxima ação:** TASK-049 concluída e seguida pela TASK-050. Coordenação
  distribuída de circuitos e infraestrutura de filas permanecem fora da V1.

### DEC-036 — Usar secret files com fonte única e menor privilégio

- **Data:** 2026-08-08
- **Ideia:** retirar credenciais do ambiente dos contêineres e conceder a cada
  serviço somente os arquivos de que necessita em `/run/secrets`.
- **Classificação:** Implementar agora.
- **Justificativa:** `.gitignore` evitava commit acidental dos `.env`, mas não
  evitava exposição por `docker inspect`, excesso de acesso do worker ou falta
  de detecção preventiva. `*_FILE` obrigatório em produção, conflito
  fail-closed, execução non-root e Gitleaks fixado fecham o risco imediato sem
  introduzir um cofre de V2. `POSTGRES_PASSWORD_FILE` inicializa volume novo,
  mas banco existente exige `ALTER ROLE` e reinício coordenado dos consumidores.
- **Próxima ação:** TASK-048 concluída; executar a TASK-049. Vault, cloud secret
  manager e rotação automática permanecem fora da V1.

### DEC-035 — Autenticar por senha sem expor segredo ao Telegram

- **Data:** 2026-08-08
- **Ideia:** usar links HTTPS descartáveis para criar, verificar, alterar e
  recuperar senha, mantendo sessões persistentes com TTL absoluto.
- **Classificação:** Implementar agora.
- **Justificativa:** o chat de bot não é um canal apropriado para receber senha.
  Um token aleatório, armazenado somente como hash e vinculado no servidor a
  usuário, Telegram e ação, permite abrir um formulário HTTPS sem confiar em
  identidade enviada pelo navegador. Argon2id, rate limiting persistente,
  transações atômicas e revogação fecham o escopo da V1 sem JWT/OAuth/MFA.
- **Próxima ação:** TASK-061 concluída; executar a TASK-048. Outro canal,
  recuperação por e-mail e autenticação multifator permanecem futuros.

### DEC-034 — Autorizar a V1 com papel único e ownership obrigatório

- **Data:** 2026-08-08
- **Ideia:** manter um único `users.role`, definir DEV como superusuário
  técnico por herança e aplicar autorização sem permitir bypass dos dados de
  outros usuários.
- **Classificação:** Implementar agora.
- **Justificativa:** `USER ⊂ ADMIN ⊂ DEV` atende às capacidades existentes sem
  introduzir múltiplos papéis, planos ou entitlements. A autenticação da
  TASK-046 precede a política fail-closed; ownership continua uma condição
  independente para todos os papéis. Recusas encerram o webhook sem efeito
  funcional e deixam somente auditoria sanitizada. A promoção do proprietário
  de ADMIN para DEV é one-shot, por UUID validado, e não vira regra de sistema.
- **Próxima ação:** TASK-047 concluída; executar a TASK-061 antes da TASK-048.
  Múltiplos papéis, planos e gestão de roles permanecem na V2.

### DEC-033 — Executar a TASK-061 depois da TASK-047

- **Data:** 2026-08-08
- **Ideia:** retirar a autenticação real por usuário e senha da fila da V1.2 e
  inseri-la no fluxo principal imediatamente depois da autorização.
- **Classificação:** Implementar agora.
- **Justificativa:** autorização por papel (TASK-047) fecha primeiro as ações
  permitidas; em seguida, a TASK-061 estabelece credenciais, verificação e
  recuperação antes das demais etapas de segurança e entrega. A ordem oficial
  passa a ser `TASK-047 → TASK-061 → TASK-048`, sem renumerar identificadores.
- **Próxima ação:** concluir a TASK-047; depois executar obrigatoriamente a
  TASK-061 antes de iniciar a TASK-048.

### DEC-032 — Autenticar a identidade mínima do canal Telegram

- **Data:** 2026-08-08
- **Ideia:** fechar a fronteira de confiança do Telegram sem antecipar a
  autenticação por senha da TASK-061 nem a autorização da TASK-047.
- **Classificação:** Implementar agora.
- **Justificativa:** o segredo autentica a entrega, mas uma identidade de
  pessoa só é aceita depois, em chat privado direto com
  `chat.id == message.from.id`. A conta interna precisa estar ativa antes de
  IA, domínio ou qualquer mutação. Recusas terminam em `204` e logam somente
  um motivo fechado, sem IDs ou payload.
- **Próxima ação:** TASK-046 concluída; a próxima tarefa executável é a
  TASK-047.

### DEC-031 — Separar métricas, traces e disponibilidade funcional

- **Data:** 2026-08-08
- **Ideia:** entregar observabilidade sem transformar o stack operacional em
  dependência da API nem duplicar métricas por OTLP.
- **Classificação:** Implementar agora.
- **Justificativa:** Prometheus faz scrape direto de API/worker; somente traces
  seguem pelo Collector ao Jaeger. Rotas e labels usam catálogos limitados,
  SQL omite valores, e `/health`/`ready` separam processo de dependência
  funcional. Regras Prometheus representam detecção de estado, não envio de
  notificação sem Alertmanager.
- **Próxima ação:** TASK-045 concluída; a próxima tarefa executável é a
  TASK-046.

### DEC-030 — Publicar branch da TASK e atualizar apenas a main local

- **Data:** 2026-08-08
- **Ideia:** eliminar a confirmação repetitiva para publicação da branch sem
  perder o controle explícito sobre a `main` remota.
- **Classificação:** Implementar agora.
- **Justificativa:** a branch da TASK é o artefato remoto de trabalho e pode ser
  publicada automaticamente após testes, revisão e commit. A integração na
  `main` local mantém o workspace pronto para a próxima tarefa. Já
  `origin/main` continua sendo o ponto de publicação controlado pelo usuário e
  nunca deve avançar sem pedido explícito.
- **Próxima ação:** ao concluir cada TASK, subir sua branch e atualizar a
  `main` local automaticamente; aguardar pedido somente para atualizar a
  `main` remota.

### DEC-029 — Separar solicitação imutável da trilha append-only de confirmação

- **Data:** 2026-08-08
- **Ideia:** persistir a confirmação da TASK-040 sem transformá-la em máquina
  de estados e resolver concorrência pelo PostgreSQL.
- **Classificação:** Implementar agora.
- **Justificativa:** `purchase_confirmations` preserva a evidência original e
  `purchase_trail_entries` registra `requested` e no máximo um terminal. O
  índice único parcial é a autoridade concorrente; o serviço usa SAVEPOINT e
  distingue pelo nome somente essa violação. Uma observação nova idêntica não
  invalida a proveniência, `cancel` independe de TTL e, em `confirm`, expiração
  precede revalidação. Isso garante recuperação e idempotência sem status
  mutável, compra, evento ou auditoria duplicada.
- **Próxima ação:** TASK-041 concluída e validada no PostgreSQL 18 real; a
  próxima tarefa executável é a TASK-045.

### DEC-028 — Vincular confirmação temporária à evidência original e fazê-la expirar

- **Data:** 2026-08-08
- **Ideia:** fechar a TASK-040 como uma confirmação explícita, temporária e
  somente em memória para qualquer oferta elegível escolhida pelo proprietário.
- **Classificação:** Implementar agora.
- **Justificativa:** vincular somente valores monetários permitiria confirmar
  uma evidência diferente que por acaso repetisse os mesmos números. A
  solicitação guarda missão, oferta, observação e proprietário, além do snapshot
  completo, e expira após 15 minutos em UTC. A resolução recalcula a comparação
  e exige os mesmos campos materiais relevantes. A TASK-041 refinou a regra:
  novo UUID de observação com conteúdo equivalente continua válido, enquanto a
  observação original permanece como proveniência; expiração ou divergência
  material produz `stale`.
- **Próxima ação:** TASK-040 concluída; persistência e concorrência foram
  integradas pela TASK-041 (`DEC-029`).

### DEC-027 — Compartilhar elegibilidade e ordenação entre recomendação e comparação

- **Data:** 2026-08-08
- **Ideia:** fazer a TASK-039 comparar todas as evidências da TASK-038 sem criar
  uma segunda interpretação de elegibilidade ou uma ordenação divergente.
- **Classificação:** Implementar agora.
- **Justificativa:** uma única função ordena as ofertas elegíveis por total,
  recência e UUID tanto para a recomendação quanto para a comparação. Assim, a
  posição 1 é invariavelmente a recomendação da TASK-038. Inelegíveis não têm
  posição, ficam depois das elegíveis e são estabilizadas sem usar preço. Frete
  desconhecido conserva o preço do produto, mas mantém o total indisponível e
  informa `shipping_unknown`; nenhuma comparação parcial é inventada.
- **Próxima ação:** TASK-039 concluída e validada no PostgreSQL real; a próxima
  tarefa executável é a TASK-040.

### DEC-026 — Restringir recomendação ao menor custo total determinável na moeda da missão

- **Data:** 2026-08-08
- **Ideia:** fechar o escopo genérico da TASK-038 como uma recomendação única e
  determinística por missão ativa, preservando a comparação completa para a
  TASK-039 e compra, confirmação e trilha para as TASKs 040 e 041.
- **Classificação:** Implementar agora.
- **Justificativa:** frete desconhecido não prova custo zero e, portanto, não
  pode vencer uma seleção por total. Moedas diferentes também não são
  comparáveis sem uma política de conversão, ausente do MVP. A recomendação usa
  somente coletas bem-sucedidas da própria missão, fontes selecionadas,
  disponibilidade atual, moeda exata do critério e frete conhecido. Vendedor é
  evidência opcional porque varejistas diretos não possuem `Seller`. Ofertas
  inelegíveis permanecem explicadas, sem formar a comparação ordenada da
  TASK-039. Ausência de candidata válida vira `insufficient_data`, nunca uma
  escolha parcial.
- **Próxima ação:** TASK-038 concluída e validada no PostgreSQL real; a próxima
  tarefa executável é a TASK-039.

### DEC-025 — Restringir a TASK-037 a preferências de notificações com skipped terminal

- **Data:** 2026-08-08
- **Ideia:** resolver a sobreposição aparente entre a TASK-037 genérica
  ("preferências de usuário") e os campos de lojas/categorias já entregues
  pela TASK-060, tornando a TASK-037 exclusivamente responsável por ativar ou
  desativar, de forma independente, notificações de queda de preço e de
  preço-alvo atingido pelo comando textual `/preferencias`.
- **Classificação:** Implementar agora.
- **Justificativa:** `docs/internal/project-context.md` já reservava explicitamente
  preferências de notificação à TASK-037, enquanto a TASK-060 possui escopo e
  validação próprios para cadastro. Ambas as notificações começam ativadas para
  preservar compatibilidade. Tratar opt-out como falha causaria retry infinito;
  ignorar sem histórico deixaria o consumo sem rastreabilidade. Por isso
  `skipped` é um resultado append-only, sem `failure_code`, e terminal como
  `succeeded`: não envia, não fica pendente e não reaparece ao reativar. O chat
  privado da TASK-036 continua sendo o único destino.
- **Próxima ação:** TASK-037 concluída e validada contra PostgreSQL e Telegram
  reais; TASK-060 permanece inalterada. A próxima executável é a TASK-038.

### DEC-024 — Restringir notificações Telegram a alertas e chats privados

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-036 como consumidor contínuo dos eventos
  `price.decreased.v1` e `price.target_reached.v1`, enviando somente para o
  chat privado confirmado do proprietário da missão.
- **Classificação:** Implementar agora.
- **Justificativa:** decisões anteriores reservam a TASK-036 às notificações
  proativas de alerta, enquanto preferências de notificação pertencem à TASK-037. Persistir
  chats de grupos/canais como destino automático poderia expor dados de uma
  missão a terceiros; por isso `telegram_chat_id` só é atualizado quando a Bot
  API identifica `chat.type=private` e o ID corresponde ao
  `telegram_user_id`. O consumidor usa a semântica at-least-once da TASK-044:
  rejeições da API e destinos ausentes/inativos ficam como falha rastreável e
  podem repetir; exatamente uma vez não é prometido.
- **Próxima ação:** TASK-036 concluída. A TASK-037 é a próxima executável e
  poderá definir preferências sem alterar a segurança do destino privado.

### DEC-023 — Adotar consumo at-least-once por consumidor com transação explícita

- **Data:** 2026-08-08
- **Ideia:** implementar a TASK-044 como uma fronteira genérica de consumo
  concorrente, registrando cada tentativa em histórico append-only e
  considerando concluído apenas o par evento/consumidor que possuir resultado
  `succeeded`.
- **Classificação:** Implementar agora.
- **Justificativa:** `FOR UPDATE SKIP LOCKED` distribui eventos entre
  transações concorrentes sem introduzir fila externa; manter reivindicação,
  processamento e registro sob a transação controlada pelo chamador preserva o
  lock até o desfecho. A semântica at-least-once permite retry após falha sem
  apagar evidência. Exactly-once, backoff, limite de tentativas, dead-letter
  queue, worker e integração Telegram aumentariam o escopo e pertencem a
  tarefas posteriores.
- **Próxima ação:** TASK-044 concluída e documentada em
  `docs/architecture/event-consumption.md`; a próxima tarefa executável volta a ser a
  TASK-036, que definirá o consumidor/notificação Telegram e o endereçamento
  por `chat_id` sem alterar este contrato genérico.

### DEC-022 — Reordenar TASK-036 atrás de TASK-043 e TASK-044, e restringir a TASK-043 à publicação genérica

- **Data:** 2026-08-08
- **Ideia:** ao iniciar o preflight da TASK-036 ("Criar notificações
  Telegram"), a próxima tarefa executável pela ordem do `docs/internal/roadmap.md`,
  descobri que ela depende de duas coisas inexistentes: um pipeline real de
  eventos persistidos/publicados (`docs/architecture/price-alerts.md` já atribuía
  "persistência e publicação" à TASK-043 e "consumo" à TASK-044) e um
  `chat_id` persistido para endereçar conversas do Telegram
  (`docs/architecture/users.md` já previa isso como responsabilidade de uma tarefa
  futura). O usuário confirmou implementar TASK-043 e TASK-044 antes de
  retomar a TASK-036. Durante a exploração para a TASK-043, ficou claro que
  nenhum dos seis tipos de evento do catálogo (TASK-042) tem hoje um
  produtor real com chamador em produção, exceto `evaluate_price_alerts`
  (TASK-027) — que também não tinha chamador, porque não existe ainda
  nenhum serviço que insira `PriceObservation` de verdade. Escopo da
  TASK-043 restrito a: tabela `events` (migração + modelo, conforme
  `docs/database/schema.md`) e um serviço genérico `publish_event`, validado
  contra PostgreSQL real usando candidatos reais de `evaluate_price_alerts`
  — sem detectar os outros cinco tipos de evento (nenhuma TASK atribui essa
  detecção ainda) e sem nenhum worker/consumidor (TASK-044).
- **Classificação:** Implementar agora.
- **Justificativa:** a numeração da TASK não substitui dependências
  explícitas (`docs/internal/roadmap.md`); implementar a TASK-036 sem um pipeline
  real de eventos e sem `chat_id` exigiria mockar exatamente o que
  `AGENTS.md` proíbe substituir por implementação incompleta. O mesmo
  padrão já foi aceito neste projeto para `AuditEntry` (TASK-016) e
  `MissionTransition` (TASK-021): tabelas append-only criadas e validadas
  contra PostgreSQL real antes de qualquer chamador de produção existir.
- **Próxima ação:** TASK-043 e TASK-044 implementadas e concluídas
  (`docs/tasks/TASK-043.md`, `docs/tasks/TASK-044.md`). A TASK-036 volta a
  ser a próxima executável, incluindo a definição do `chat_id`.

### DEC-021 — Criar a fase V1.2 com uma lista priorizada de evoluções entre a V1 e a V2

> **Atualização (DEC-033):** a TASK-061 foi retirada desta fase e inserida no
> fluxo principal, imediatamente após a TASK-047. Os demais itens da V1.2
> mantêm sua ordem relativa original.

- **Data:** 2026-08-08
- **Ideia:** o usuário pediu um documento próprio para uma fase "V1.2",
  que sai depois da V1 e antes da V2, com ordem de execução definida. A ordem
  original começava pela TASK-061 (posição depois supersedida pela DEC-033),
  seguida por: ajustar o cadastro para
  pedir e-mail visando notificações; enviar notificações por e-mail;
  pesquisa de cupons; e um painel administrativo web com acesso/edição
  direta ao banco, login de administrador, status/consumo da aplicação e
  controles operacionais (reiniciar aplicação/banco). Pediu para pensar em
  quantos itens fazem sentido para o painel, sem criar um arquivo de TASK
  por item agora — só listar dentro do próprio documento da V1.2.
- **Classificação:** Versão futura.
- **Justificativa:** nenhum destes itens está em `docs/internal/mvp.md` (a V1 só
  prevê notificações essenciais via Telegram, não e-mail nem cupons nem
  painel administrativo). Diferente do registro sem compromisso do
  `docs/internal/backlog.md`, o usuário quer prioridade e ordem definidas — por
  isso ganham um documento próprio (`docs/internal/v1.2-scope.md`) com a lista já
  ordenada, sem criar `docs/tasks/TASK-XXX.md` individuais ainda; cada
  item vira TASK de verdade (com preflight, validação e critério de
  aceite próprios) só quando for solicitado para execução.
- **Próxima ação:** criado `docs/internal/v1.2-scope.md` com a ordem de execução e a
  decomposição do painel administrativo; nenhuma implementação iniciada.

## Registros

### DEC-020 — Permitir armazenar e-mail em User, mantendo senha e token de fora

- **Data:** 2026-08-08
- **Ideia:** `docs/architecture/users.md` ("Limites") registrava "Não são armazenadas
  senhas, tokens, e-mails ou credenciais de autenticação real" como um
  invariante único. O cadastro inicial da TASK-060 pede e-mail como campo
  não sensível; senha/token continuam explicitamente fora (TASK-061,
  `DEC-019`). É preciso separar e-mail (dado pessoal comum) desse
  invariante, que na origem tratava tudo como "credencial de autenticação".
- **Classificação:** Implementar agora.
- **Justificativa:** e-mail não é, por si só, uma credencial de
  autenticação — é dado pessoal padrão em cadastros, e o usuário confirmou
  explicitamente querer incluí-lo na TASK-060 mesmo sabendo do invariante
  anterior. Senha e token continuam de fora, sem mudança nenhuma nessa
  parte. `docs/architecture/users.md` será atualizado para refletir a separação.
- **Próxima ação:** atualizar `docs/architecture/users.md` e `docs/database/schema.md`
  removendo "e-mails" da lista de dados não armazenados, mantendo
  senha/token/credenciais de autenticação real de fora.

### DEC-019 — Criar a TASK-061 para autenticação real por usuário e senha

> **Atualização (DEC-033):** a tarefa deixou de aguardar a retomada da V1.2 e
> passou a ser obrigatória depois da TASK-047 e antes da TASK-048.

- **Data:** 2026-08-08
- **Ideia:** ao detalhar os campos do cadastro inicial da TASK-060, o
  usuário pediu também uma senha para autenticar no bot (usuário + senha),
  "pra saber que é ele mesmo".
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** autenticação real por senha não é "dado não sensível"
  — exige hashing seguro (nunca texto puro), fluxo de verificação e
  provavelmente recuperação de conta; é uma peça de segurança com desenho
  próprio, não um campo a mais num cadastro. `docs/internal/project-context.md`
  ("O que não existe") já registra que não há autenticação real hoje, de
  propósito — a identidade via Telegram (`User.telegram_user_id`, TASK-056)
  já é confiável para o canal atual. O usuário concordou em tirar isso da
  TASK-060 e tratar como TASK própria quando pedir.
- **Próxima ação:** criada `docs/tasks/TASK-061.md` (stub, escopo a definir
  na validação); pela DEC-033, executá-la depois da TASK-047 e antes da
  TASK-048.

### DEC-018 — Criar a TASK-060 para seleção de perfil de IA por papel, cadastro inicial e placeholder de upgrade

- **Data:** 2026-08-08
- **Ideia:** três pedidos relacionados do usuário, feitos juntos ao pedir
  para executar a TASK-058: (1) o webhook do Telegram passar a escolher o
  perfil de IA (`USER` vs `ADMIN`/`DEV`) a partir de `User.role`, em vez de
  sempre usar `USER` fixo, e o usuário (dono do projeto) ter seu próprio
  `User` elevado para `ADMIN` manualmente; (2) um fluxo de cadastro inicial
  via Telegram, capturando dados não sensíveis a definir; (3) um comando ou
  opção de "mudar de usuário/perfil" visível ao usuário, mas inativo —
  reservado para uma futura oferta de upgrade, gratuita por enquanto.
- **Classificação:** Nova TASK do MVP.
- **Justificativa:** mudar de qual perfil de IA uma interação real usa é
  uma alteração de comportamento de produção no despacho do webhook (TASKs
  033–035), afetando diretamente o invariante que separava `USER` (Gemini
  gratuito, sem fallback, reservado a usuários reais) de `ADMIN/DEV`
  (cascata premium/Groq, TASK-059) — precisa de análise própria de como o
  papel é determinado e protegido, não pode ser um ajuste dentro de outra
  TASK. O cadastro inicial introduz persistência nova (campos ainda a
  definir) e também exige TASK própria. O placeholder de "mudar de
  usuário/perfil" fica deliberadamente **inativo** — nenhuma lógica de
  cobrança, plano pago ou mudança real de papel por autoatendimento — para
  não violar `docs/internal/out-of-scope.md` ("Plano PLUS", "Usuário pago"), que
  reserva qualquer oferta paga para a V2; é só uma afordance visível,
  reservada para decisão futura.
- **Próxima ação:** criada `docs/tasks/TASK-060.md`, registrada no
  roadmap; a elevação manual do usuário do próprio dono do projeto para
  `ADMIN` é uma ação pontual e deliberada dentro desta TASK, não uma
  capacidade geral exposta a qualquer usuário.

### DEC-017 — Encerrar a TASK-057 com validação real parcial e adiar mais variedade de linguagem para a V2

- **Data:** 2026-08-08
- **Ideia:** encerrar a TASK-057 aceitando a cobertura real atual — 3 dos 4
  valores de `IntentKind` confirmados contra o `USER`/Gemini real
  (`create_mission`, `query_mission`, `mission_command`); `unknown` só foi
  confirmado via a cascata `ADMIN/DEV` (Gemini premium/Groq), não contra o
  Gemini gratuito real do `USER`, por esgotamento repetido da cota
  gratuita. Testar ainda mais tipos/estilos de linguagem informal fica para
  a V2, em vez de continuar tentando fechar 100% da cobertura agora.
- **Classificação:** Versão futura (para a ampliação adicional de
  variedade de linguagem); o encerramento da TASK-057 em si é uma decisão
  de aceite explícita do usuário, não uma nova funcionalidade.
- **Justificativa:** o usuário autorizou explicitamente encerrar a TASK-057
  nesse estado ("dá a task 57 como encerrada... qualquer coisa na v2 a
  gente testa mais tipos de linguagens"). O prompt do `IntentInterpreter`
  foi refinado e validado com sucesso para os quatro `IntentKind` via
  provedores reais (`ADMIN/DEV`: Gemini premium e Groq; `USER`: Gemini
  gratuito para 3 dos 4 tipos), a suíte automatizada está aprovada, e a
  ferramenta de validação (`--profile admin`, TASK-059) já reduziu bastante
  o risco de regressão futura sem depender da cota escassa do `USER`. Não
  há indício de defeito conhecido no `unknown` — a lacuna é só de cobertura
  de confirmação real, não de comportamento incorreto observado.
- **Próxima ação:** `docs/tasks/TASK-057.md` marcada como concluída;
  registrar em `docs/internal/backlog.md` a ampliação futura de variedade de
  linguagem/gírias do `IntentInterpreter` para a V2.

### DEC-016 — Criar a TASK-059 para avaliar o Groq como fallback de cota do AIProviderManager

- **Data:** 2026-08-08
- **Ideia:** durante o impedimento de cota da TASK-057, o usuário pediu para
  testar a chave `AISHOPPING_GROQ_API_KEY` já presente em `backend/.env`,
  fora do `AIProviderManager` e sem tocar em nenhum módulo do app (script
  descartável em `scratchpad`, nunca importado por `backend/app`). A chave
  respondeu `200` com conteúdo coerente (`openai/gpt-oss-120b`). O
  usuário pediu para registrar a possibilidade de usar o Groq como fallback
  para quando a cota gratuita do Gemini se esgotar.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** um `GroqProvider` dentro de `app.ai_provider` seguindo
  o mesmo contrato agnóstico (`AIProvider`) já usado pelo Gemini é uma
  mudança arquitetural real no `AIProviderManager`, não um ajuste mecânico
  — por isso não pode ser implementada dentro de outra TASK, mesmo
  padrão que justificou TASK própria para TASK-056/057/058. Há tensão
  explícita com o invariante já documentado em `docs/architecture/ai-provider-manager.md`
  ("OpenAI, Claude, usuário pago e comparação multi-IA ficam para a V2") e
  em `docs/internal/project-context.md` ("USER usa apenas Gemini gratuito... OpenAI,
  Claude e usuário pago ficam para a V2"): embora o Groq não esteja citado
  nominalmente nessas exclusões, o princípio por trás delas — a V1 usa
  somente o Gemini como provedor de IA — precisa ser revisto
  explicitamente antes de qualquer implementação, não assumido por
  conveniência de disponibilidade de uma chave. A motivação é real e
  relevante ao MVP (a cota gratuita do Gemini se mostrou frágil o bastante
  nesta própria sessão para bloquear validação real), mas a decisão de
  introduzir um segundo provedor no MVP exige análise própria de escopo,
  custo, confiabilidade e consistência de contrato entre respostas de
  provedores diferentes.
- **Próxima ação:** criada `docs/tasks/TASK-059.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada — incluindo, como parte
  da própria TASK, decidir se o invariante "só Gemini na V1" deve ser
  revisto.

### DEC-015 — Criar a TASK-058 para confirmação da intenção interpretada antes da execução

- **Data:** 2026-08-08
- **Ideia:** ao pedir a execução da TASK-057, o usuário pediu também que a IA
  devolva o texto interpretado da intenção e peça confirmação explícita de
  que é aquilo que a pessoa quer, antes de criar, consultar ou comandar
  qualquer missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** é uma mudança de comportamento no fluxo de despacho do
  webhook (resposta síncrona ao comando do usuário), explicitamente fora do
  escopo da TASK-057 (`docs/tasks/TASK-057.md`, seção "Fora de escopo": "Não
  altera `app.telegram`... TASKs 033 a 035") e não coberta pela TASK-036
  (notificações proativas de alerta) nem pela TASK-037 (preferências de notificação).
  Confirmação de intenção antes de agir é uma decisão de domínio nova e
  não-trivial — mesmo padrão que justificou TASK própria para a identidade
  do Telegram (`DEC-011`) — não um refinamento mecânico que caiba em outra
  TASK já registrada. O usuário optou explicitamente por executar apenas a
  TASK-057 agora e registrar esta ideia para decisão futura, sem
  implementá-la agora.
- **Próxima ação:** criada `docs/tasks/TASK-058.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

**Adendo de 2026-08-15 (correção pontual, sem nova TASK):** a entrada do
Telegram deixou de usar IA como roteador universal. Criação por linguagem
natural só ocorre depois de `/criar_missao`; cancelamento usa
`/cancelar_missao`; escolhas numéricas e confirmações usam vocabulário local
fechado. O antigo propósito `interpret_confirmation_reply` não é mais chamado.
O menu formal usa underscore porque `BotCommand.command` não aceita hífen;
aliases com hífen continuam aceitos como texto digitado.

### DEC-014 — Criar a TASK-057 para melhorar a robustez da interpretação de intenção

- **Data:** 2026-08-08
- **Ideia:** durante a validação manual real da TASK-035, uma mensagem real
  do usuário foi classificada como `unknown` quando, na avaliação do
  usuário, deveria ter sido reconhecida — o `IntentInterpreter` (TASK-032)
  precisa ficar mais robusto para diferentes formas de escrita.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** o usuário pediu explicitamente o registro como próxima
  tarefa, não a correção imediata. O `IntentInterpreter` já foi validado
  contra o Gemini real nas TASKs 032, 034 e 035 para as mensagens testadas;
  isso é um refinamento de qualidade de classificação, não um defeito
  estrutural, e não deve ser implementado sem uma TASK própria — alterar o
  prompt de sistema durante a validação da TASK-035 misturaria escopos e
  arriscaria regressão sem a validação dedicada que a mudança merece.
- **Próxima ação:** criada `docs/tasks/TASK-057.md`, registrada no roadmap;
  aguarda solicitação explícita para ser executada.

### DEC-013 — Distinguir erro conhecido de falha inesperada no despacho de missão do webhook

- **Data:** 2026-08-08
- **Ideia:** ao capturar exceções no despacho de `Intent` por comando de
  missão (TASK-035), a rota do webhook só deve tratar como resposta
  controlada (`204` + mensagem ao usuário) os erros **esperados e conhecidos**
  de domínio/validação. Qualquer falha inesperada — incluindo `IntegrityError`
  residual não tratada no serviço apropriado — não deve ser mascarada.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário ao revisar o plano da TASK-035: uma
  captura genérica de exceções esconderia bugs reais atrás de um `204`
  aparentemente saudável. A única corrida esperada com `IntegrityError`
  continua isolada dentro de `get_or_create_telegram_user` (TASK-056, via
  `SAVEPOINT`); tudo o mais que chegar até a rota como `IntegrityError` é
  inesperado e deve subir como `500`. A lista de exceções conhecidas é
  fechada e explícita: `MissionNotFoundError`, `MissionVersionConflictError`,
  `InvalidMissionTransitionError`, `MissionTransitionConditionError`,
  `MissionReferenceError` e `MissionIntentError`.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/mission-commands.md` e
  implementado em `backend/app/telegram/router.py`
  (`_KNOWN_DISPATCH_ERRORS`).

### DEC-012 — Fechar o escopo da TASK-035 (comandos de missão via Telegram)

- **Data:** 2026-08-08
- **Ideia:** `docs/tasks/TASK-035.md` só trazia uma frase de escopo real
  (seleção de fontes). Quatro decisões precisaram ser fechadas antes de
  implementar: (1) sem teclado interativo agora — as fontes vêm do que o
  `IntentInterpreter` já extrai do texto livre; (2) a TASK-035 envia uma
  resposta síncrona mínima ao Telegram, e não a TASK-036; (3) o seed das
  quatro lojas da V1 entra nesta TASK, por ser dado de referência fixo já
  definido em `docs/architecture/providers.md`; (4) quando o `Intent` de criação
  não especifica nenhuma fonte, a missão usa automaticamente as quatro
  fontes da V1 e sai `active` — nunca fica em `draft` por falta de fonte.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/internal/mvp.md` exige que "um usuário autorizado consegue
  criar e consultar uma missão pelo canal Telegram" — sem resposta síncrona,
  "consultar" não tem como funcionar para o usuário. `TASK-036` continua
  reservada a notificações proativas orientadas a evento (alertas de preço,
  TASK-027/042-044), não a essa resposta ao próprio comando do usuário. O
  seed de lojas é dado de referência fixo, sem decisão de domínio nova,
  diferente do que justificou uma TASK própria para a identidade do Telegram
  (`DEC-011`). Toda `CREATE_MISSION` válida sair `active` evita o estado
  intermediário "criada mas inerte" que uma missão em `draft` sem fonte
  representaria.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/mission-commands.md` e
  `docs/tasks/TASK-035.md`.

### DEC-011 — Criar a TASK-056 para vincular identidade do usuário ao Telegram antes da TASK-035

- **Data:** 2026-08-07
- **Ideia:** ao preparar a TASK-035 ("Criar comandos de missão"), identifiquei
  que persistir uma missão via Telegram exige `Mission.user_id`, uma FK
  obrigatória para `User`. `docs/architecture/users.md` hoje declara explicitamente que
  nenhum identificador do Telegram é armazenado e que não existem serviços de
  CRUD de usuário. Sem resolver qual `User` corresponde a um chat do
  Telegram, a TASK-035 não tem como gravar o proprietário da missão.
- **Classificação:** Nova TASK do MVP
- **Justificativa:** `docs/internal/mvp.md` exige, como critério objetivo de conclusão
  do MVP, que "um usuário autorizado consegue criar e consultar uma missão
  pelo canal Telegram" — isso pressupõe uma identidade resolvível, que ainda
  não existe. Não é autenticação real (reservada à TASK-046) nem autorização
  (TASK-047): é o vínculo mínimo necessário para o próximo passo do fluxo já
  iniciado nas TASKs 032 a 034. O usuário, ao ser consultado, optou por pausar
  a TASK-035 e criar esta tarefa prévia em vez de ampliar o escopo da 035 ou
  implementar apenas a camada de apresentação sem persistência.
- **Próxima ação:** criada `docs/tasks/TASK-056.md`, fora da faixa numérica
  original (mesmo padrão da TASK-042 e da TASK-055), posicionada no roadmap
  imediatamente antes da TASK-035, que permanece bloqueada até a TASK-056 ser
  executada. `docs/internal/roadmap.md`, `docs/tasks/README.md`, `AGENTS.md` e
  `docs/architecture/users.md` atualizados para refletir a pendência.

### DEC-010 — Nunca converter falha de interpretação em falha de transporte no webhook

- **Data:** 2026-08-07
- **Ideia:** depois que o webhook do Telegram autentica e aceita uma
  atualização, uma falha subsequente ao traduzi-la em `Intent` (cota de IA
  excedida, indisponibilidade do provedor, conteúdo inválido) não deve virar
  `500`. A entrega da atualização pelo Telegram e o processamento por IA são
  tratados como falhas independentes.
- **Classificação:** Implementar agora
- **Justificativa:** decisão do usuário durante a aprovação do plano da
  TASK-034: um `500` faria o Telegram reentregar a mesma atualização,
  potencialmente repetindo a chamada de IA sem necessidade. A falha já é
  registrada pela telemetria sanitizada da TASK-031; a rota apenas confirma o
  recebimento com `204`, sem executar ação de domínio, sem responder ao
  usuário e sem criar mecanismo de retry, fila ou execução de comando — isso
  permanece reservado às TASK-035 e TASK-036.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/telegram-adapter.md` e
  `docs/tasks/TASK-034.md`.

### DEC-009 — Restringir a TASK-033 à fronteira de entrada do Telegram

- **Data:** 2026-08-07
- **Ideia:** `docs/tasks/TASK-033.md` só continha o texto-modelo genérico
  ("Definir adaptação Telegram"), sem escopo detalhado. Definir o que essa
  tarefa cobre exclusivamente a partir do que já está documentado:
  representar a mensagem bruta do Telegram como contrato imutável e
  traduzi-la em um `Intent`, chamando somente o `IntentInterpreter` já
  existente (TASK-032).
- **Classificação:** Implementar agora
- **Justificativa:** `docs/architecture/overview.md` lista Telegram como módulo-alvo
  com fronteira própria; `docs/architecture/telegram.md` já definia que "o adaptador deve
  traduzir mensagens em comandos ou intenções sem conter lógica de
  domínio" e que autenticação, webhooks, comandos e notificações ficam para
  tarefas posteriores. `docs/internal/roadmap.md` já reserva a TASK-034 para o
  webhook real, a TASK-035 para comandos de missão, a TASK-036 para
  notificações e a TASK-037 para preferências de notificação. Incluir qualquer
  uma dessas responsabilidades na TASK-033 seria antecipar tarefas futuras,
  proibido por `AGENTS.md`.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/telegram-adapter.md` e
  `docs/tasks/TASK-033.md`. Webhook, comandos, notificações e preferências
  pertencem às TASKs 034 a 037.

### DEC-008 — Fechar o vocabulário de intenção da TASK-032 na documentação existente

- **Data:** 2026-08-07
- **Ideia:** definir o conjunto de `IntentKind` e parâmetros da interpretação
  de intenção estritamente a partir do que já estava documentado, sem
  adicionar nem omitir nada.
- **Classificação:** Implementar agora
- **Justificativa:** `docs/architecture/mission-system.md` já define os seis comandos
  fechados de `MissionCommand` (`activate`, `pause`, `resume`, `complete`,
  `cancel`, `expire`); `docs/internal/mvp.md` exige explicitamente que o usuário
  consiga "criar e consultar uma missão pelo canal Telegram"; e
  `docs/architecture/telegram.md` fixa as quatro fontes selecionáveis da V1. `IntentKind`
  reaproveita `MissionCommand` diretamente em vez de duplicar suas strings, e
  `IntentParameters` reaproveita os campos já existentes de
  `MissionCriteria` e `mission_sources`. Nenhum campo, comando ou fonte novos
  de domínio foram introduzidos.
- **Próxima ação:** nenhuma; documentado em `docs/architecture/intent-interpretation.md` e
  `docs/tasks/TASK-032.md`. Decisões de execução de comando e de canal
  pertencem às TASKs 033 em diante.

### DEC-007 — Limitar a V1 ao Gemini por nível de acesso

- **Data:** 2026-08-02
- **Ideia:** usar Gemini gratuito para USER e permitir que ADMIN/DEV tentem o melhor modelo Gemini, com retorno automático ao gratuito quando o nível pago não estiver disponível.
- **Classificação:** Implementar agora
- **Justificativa:** Gemini 3.6 Flash possui nível gratuito e já foi validado, enquanto OpenAI e Anthropic não oferecem API geral gratuita nas contas configuradas. A política mantém uma integração real na V1, evita dependência de créditos e preserva um único comportamento compartilhado para ADMIN/DEV.
- **Próxima ação:** implementar na TASK-030 USER somente com `gemini-3.6-flash` e ADMIN/DEV tentando `gemini-3.1-pro-preview` antes do fallback gratuito; registrar usuário pago e OpenAI/Claude como evolução da V2.

### DEC-006 — Corrigir a TASK-055 para fontes selecionadas pelo usuário

- **Data:** 2026-08-02
- **Ideia:** substituir o escopo exclusivo da Kabum por coleta em todas as lojas e marketplaces explicitamente selecionados pelo usuário para a V1.
- **Classificação:** Implementar agora
- **Justificativa:** a TASK-055 registrada anteriormente não representa o requisito informado pelo usuário. A correção amplia arquitetura, testes e manutenção, pois cada fonte exige um Store Provider próprio, mas continua limitada à seleção explícita e não autoriza descoberta ou integração automática de qualquer marketplace.
- **Próxima ação:** implementar na TASK-055 providers para Pichau, Terabyte, Amazon e Kabum; na TASK-035, exibir essas opções como selecionáveis e apresentar abaixo, sob ***Futuro***, Mercado Livre, Shopee e AliExpress desabilitados.

### DEC-005 — Ordenar observações de preço após coletas persistidas

- **Data:** 2026-08-02
- **Ideia:** corrigir a ordem de execução da TASK-015 para respeitar sua FK obrigatória para `collection_runs`.
- **Classificação:** Implementar agora
- **Justificativa:** executar a TASK-015 antes da TASK-026 exigiria antecipar persistência de coletas ou violar o contrato relacional e a rastreabilidade histórica definidos na TASK-010.
- **Próxima ação:** executar a TASK-016 antes do bloco de missões, seguir da TASK-019 à TASK-026, executar então a TASK-015 e, na sequência, a TASK-017.

### DEC-001 — Instituir governança de novas funcionalidades

- **Data:** 2026-08-01
- **Ideia:** registrar decisões e classificar previamente toda nova funcionalidade sugerida.
- **Classificação:** Implementar agora
- **Justificativa:** a política protege o escopo definido em `docs/internal/mvp.md`, evita aumento de complexidade não planejado e cria rastreabilidade para decisões futuras.
- **Próxima ação:** aplicar a política no `AGENTS.md`; registrar ideias futuras em `docs/internal/backlog.md`, `docs/internal/out-of-scope.md` ou no roadmap conforme sua classificação.

### DEC-002 — Instituir workflow permanente de execução de TASKs

- **Data:** 2026-08-01
- **Ideia:** padronizar preparação, validação, implementação, testes, revisão, documentação, commit e push para toda TASK.
- **Classificação:** Implementar agora
- **Justificativa:** o workflow preserva o escopo do MVP, aumenta a rastreabilidade das entregas e garante que código, documentação e repositório permaneçam sincronizados.
- **Próxima ação:** aplicar automaticamente o workflow definido em `AGENTS.md`
  a toda TASK futura. A política de push foi posteriormente refinada pela
  `DEC-030`.

### DEC-003 — Inventariar dependências para novas máquinas

- **Data:** 2026-08-01
- **Ideia:** registrar dependências e verificar a compatibilidade do ambiente antes de iniciar TASKs em outra máquina.
- **Classificação:** Implementar agora
- **Justificativa:** evita instalações desnecessárias, mantém o ambiente reproduzível e preserva a autorização do usuário para qualquer download ou instalação.
- **Próxima ação:** manter `docs/development/dependencies.md` e `backend/requirements.txt` atualizados; comparar o ambiente antes de cada nova TASK.

### DEC-004 — Acompanhar a versão estável mais recente do Python

- **Data:** 2026-08-01
- **Ideia:** manter o projeto na versão estável mais recente do Python, em vez de fixá-lo permanentemente em uma série menor antiga.
- **Classificação:** Implementar agora
- **Justificativa:** a alteração afeta somente a política de ambiente, não amplia o escopo funcional do MVP e evita instalar uma versão antiga quando a versão estável atual é compatível. Cada atualização continua condicionada à validação das dependências e dos testes aplicáveis.
- **Próxima ação:** registrar em `docs/development/dependencies.md` a versão mais recente efetivamente validada e repetir a validação quando uma nova versão estável for adotada.
### DEC-061 — Compartilhar roteamento gratuito entre USER e DEV

- **Data:** 2026-08-15
- **Ideia:** manter toda IA atrás do `AIProviderManager`, ampliar o perfil
  `USER` exclusivamente com fallbacks gratuitos
  Gemini → Groq (`openai/gpt-oss-120b`) → OpenRouter (`openrouter/free`) e
  dar ao perfil `DEV` a mesma capacidade gratuita. Em requisições normais,
  ambos seguem Gemini → Groq (`openai/gpt-oss-120b`) → OpenRouter
  (`openrouter/free`). Pesquisa web é exclusiva de DEV e opt-in por
  `AIRequest.require_search_grounding`; nesse caso a requisição vai diretamente
  à Firecrawl Search API v2 direta e, após fontes válidas, à mesma cascata
  gratuita de LLM. `ADMIN`, papel histórico do domínio, compartilha a mesma cascata
  gratuita e não constitui uma terceira política de IA.
- **Classificação:** Implementar agora
- **Justificativa:** pedido explícito do usuário para corrigir a continuidade
  após timeout/HTTP 504 do Gemini, remover o modelo Groq antigo e impedir uso
  intencional de modelos pagos. A mudança reutiliza contratos,
  circuit breaker, telemetria e capability de grounding já existentes; não
  altera fluxos determinísticos do Telegram, coleta, preços ou missões.
- **Próxima ação:** implementar e validar somente o roteamento descrito, sem
  no máximo duas chamadas reais gratuitas e controladas, sem push, rebuild ou
  deploy. A TASK-086 do drift conhecido do `alembic check` permanece não iniciada.
