# Backlog de Evoluções Futuras

> Estado em 2026-08-17: TASK-077, TASK-084 e TASK-089 estão concluídas
> (release `v1.0.7`). O limite WSL2 e a política de não habilitar
> schedules de missões terminais são estado operacional, não novos itens de
> backlog.

> Estado em 2026-08-21 (`DEC-072`): a V1.2 foi reorganizada em torno de uma
> aplicação web completa (`docs/internal/v1.2-scope.md`) — o item "Dashboard
> web para acompanhamento de missões, ofertas e histórico", antes registrado
> aqui como ideia solta da V2, passa a ser o próprio núcleo da V1.2 (itens 1,
> 2, 4, 6, 7 e 8 daquele documento) e foi removido deste backlog para não
> duplicar/contradizer o escopo. Em contrapartida, os itens de e-mail (opt-in
> de notificação no cadastro e notificações por e-mail de fato) saíram da
> V1.2 e entraram aqui, na V2.

> Ajuste de 2026-08-22 (`DEC-080`): frete/parcelamento autenticado e pesquisa
> de ofertas em lives também saíram da V1.2 e foram movidos para a V2. Magalu,
> Mercado Livre e Shopee permanecem como novas fontes planejadas para a V1.2;
> AliExpress não entra nessa etapa.

> Estado em 2026-08-22 (`DEC-089`/`DEC-090`/`DEC-091`/`DEC-092`): das três
> fontes acima, Magalu (TASK-104A) e Mercado Livre (TASK-104B) estão
> concluídas e publicadas. Shopee (TASK-104C) foi **adiada**, não
> cancelada — auditoria real sem evasão confirmou CAPTCHA
> (`scene=crawler_item`) mesmo com Edge/CDP autenticado e sessão
> persistente; nenhum código de provider foi escrito. Fica registrada
> aqui como candidata a retomar quando existir uma abordagem sem evasão
> (ex.: API oficial/parceria). AliExpress continua fora.

Este documento é o repositório de ideias que surgirem durante o desenvolvimento e não pertencem à versão atual. Registrar uma ideia aqui não a aprova, não cria uma TASK e não altera o escopo da V1.

Para cada item novo, registrar uma descrição curta, a motivação e eventuais dependências. A priorização e a transformação em tarefa dependem de decisão explícita posterior.

## Novas fontes de oferta

- Store Provider para AliExpress, ainda futuro e fora da V1.2.
- Inclusão após a V1 de lojas ou marketplaces que não forem selecionados para a TASK-055.
- Critérios de qualificação, confiabilidade e manutenção de Store Providers.
- Evolução da normalização de vendedores, frete, impostos, prazo e políticas específicas além do necessário às fontes selecionadas.
- **Frete e parcelamento autenticados** (`DEC-045`/`DEC-080`): toda consulta
  autenticada, inclusive a ferramenta inicialmente imaginada para DEV/ADMIN,
  foi movida para a V2. Exige projeto próprio de
  credenciais/sessões isoladas por usuário, autorização, proteção de dados
  e ciclo de vida das sessões, além de regras específicas por marketplace —
  nada disso será antecipado na V1.2.

## Canais e experiência do usuário

- Integração com WhatsApp, aplicativo mobile e multi-idioma — avaliar se
  ainda fazem sentido como capabilities separadas diante da aplicação web
  que passa a ser o núcleo da V1.2 (`docs/internal/v1.2-scope.md`, `DEC-072`);
  não descartados, só precisam de revisão quando a V1.2 estiver mais
  avançada.
- **E-mail (movido da V1.2 para a V2 em `DEC-072`)**: opt-in de notificação
  por e-mail no `/cadastro` e envio de notificações por e-mail de fato,
  condicionado a esse opt-in — a V1.2 original previa isso como os dois
  primeiros itens da lista ordenada; com o novo núcleo da V1.2 sendo a
  aplicação web (que não depende de e-mail para existir), o usuário decidiu
  adiar toda a capability de e-mail para a V2, unificada com o item de
  confirmação/verificação abaixo.
- Confirmação/verificação de e-mail no onboarding da V2; o e-mail da V1
  permanece opcional, não verificado e sem uso para recuperação.
- **Pesquisa de ofertas em lives** (`DEC-056`/`DEC-080`): buscar promoções
  anunciadas em transmissões ao vivo fica para a V2. O escopo anteriormente
  limitado a YouTube e Shopee Live continua sendo referência inicial, mas não
  cria integração na V1.2 nem se confunde com o Store Provider da Shopee.

## Inteligência e automação

- Na V1.2, tratar missões sem valor informado com uma pergunta explícita de
  orçamento. Se a pessoa não possuir um valor, consultar primeiro o histórico
  elegível e depois fontes externas reais para propor uma referência de mercado
  de itens/marcas mais baratos, com regras de evidência e dados insuficientes a
  definir em TASK própria (`DEC-044`).
- Ampliar ainda mais a robustez do `IntentInterpreter` (TASK-032/057) para
  novos tipos e estilos de linguagem informal além do conjunto já validado,
  com nova rodada de validação real (`DEC-017`).
- Perfil pago para usuários na V2, com créditos e entitlements próprios.
  A `DEC-061` mantém USER e DEV exclusivamente gratuitos na V1.
- OCR para extrair informações de imagens, comprovantes ou páginas.
- IA local.
- Plano PLUS com uso de múltiplos provedores de IA.
- Sistema de plugins para extensões de fontes, canais e comportamentos.

## Dados e análise

- **V2 de parcelamento (TASK-089/`DEC-069` concluída na `v1.0.7`)**: a
  V1 já coleta, persiste (`offer_installment_options`, 1:N por
  observação) e apresenta nas mensagens do Telegram (`💰 À vista`/
  `💳 Parcelado`) as condições reais de parcelamento, sem inferência.
  Ficam para uma V2 futura, explicitamente fora desta release: novo
  `IntentKind` para o usuário escolher uma condição de parcelamento;
  interpretação de frases como "quero em 6x"/"quero parcelado"; um
  mecanismo de rastreamento de "oferta apresentada" por usuário/chat/
  missão (hoje inexistente no projeto); integração dessa escolha com o
  fluxo de compra/confirmação (`purchase/confirmation.py`); qualquer
  análise (por IA ou determinística) de custo-benefício, comparação
  entre condições ou "melhor parcelamento".
- Analytics avançado de preços e comportamento de compra.
- Relatórios exportáveis e agendados.
- Comparador de preços avançado, com critérios configuráveis e visualizações históricas.

## Plataforma e integrações

- API pública.
- Sistema de pagamentos.
- Integrações adicionais não essenciais à V1.

## Papéis e planos da V2

A V1.2 usa só a divisão USER x DEV/ADMIN exclusivo (autorização já
existente, papel único) — nenhum dos itens abaixo faz parte dela (`DEC-073`).

- múltiplos papéis por usuário e tabela `user_roles`;
- composição simultânea de USER, ADMIN e DEV;
- planos FREE, PLUS e PRO, assinatura e entitlements;
- gestão administrativa explícita de papéis;
- separação estrutural avançada entre papéis de autorização e perfis de IA;
- ferramentas cross-user de suporte/auditoria somente com permissão própria.

## Regra de uso

### Operação e custos do GG Oferta após a sequência 118 — Backlog

Registrado em 2026-09-04 por decisão explícita: somente pendência futura,
sem implementação nesta continuação Core-only da TASK-118H.

1. Definir política de custo Firecrawl (orçamento, limites e responsáveis).
2. Consolidar métricas de créditos reportados/consumidos, distinguindo Search
   de enriquecimento e sem estimar créditos como se fossem reportados.
3. Validar critérios operacionais de fallback: indisponibilidade real versus
   auth/policy/quota/vazio válido; não mudar o comportamento nesta rodada.
4. Avaliar custo/uso de `/v2/scrape` como enriquecimento separado de Search,
   preservando a regra de evidência e o limite atual de URLs até nova decisão.
5. Planejar validação de volume/latência em PROD, mediante autorização própria.
6. Planejar rollout real com secrets, Cloudflare, rede e rollback; a validação
   DEV não equivale a deployment nem autoriza alteração de produção.
7. Acompanhar disponibilidade de providers externos com critérios e alertas
   operacionais a definir na task futura.

Dependência a considerar: Core ADR 0018 troca quota volátil por Redis durável.
O teste histórico GG que esperava reset da quota após restart ficou obsoleto
e deverá ser atualizado quando os contracts GG forem retomados. Core agora
distingue `429 quota_exceeded` de `503 quota_store_unavailable`, ambos sem
upstream. Avaliar explicitamente a classificação deste último no consumidor:
o tratamento genérico atual de 503 pode acionar fallback. Nenhuma alteração
de fallback, Firecrawl, Market Research, código ou teste GG foi feita aqui.

Tudo que não constar em `docs/internal/mvp.md` é considerado fora do escopo da V1. Quando uma ideia for explicitamente descartada para a V1, ela deve ser registrada também em `docs/internal/out-of-scope.md`.
