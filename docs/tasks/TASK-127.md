# TASK-127 — Exibir preço histórico (interno + externo) para todos + botão DEV para buscar sob demanda

Status: **Registrada (2026-09-21), escopo definido, implementação NÃO
iniciada.**

## Contexto real (mecanismo já existe, hoje nunca é exposto)

O F1 (`historical_bootstrap`) já faz praticamente o que foi pedido — a
lacuna real é que ele só roda automaticamente dentro do fluxo de
coleta, e nada do que ele produz chega a aparecer para o usuário:

- **Preço histórico interno** ("registrado no GG"):
  `get_internal_historical_best` (`backend/app/alerts/internal_
  history.py:35-56`) — `min(PriceObservation.amount)` só condição
  `NEW`/disponível/mesma moeda, PRODUCT-GLOBAL (nunca filtrado por
  relevância de uma Mission específica). Já existe, já é usado
  internamente pelo gatilho de pesquisa de mercado — nunca exposto ao
  usuário.
- **Preço histórico externo** ("de referência"/mercado):
  `get_external_price_reference_evidence` (`backend/app/historical_
  bootstrap/service.py:72-99`) **já devolve o MENOR preço** entre todas
  as `ExternalPriceReference` coletadas para o produto
  (`order_by(amount.asc()).limit(1)`) — ou seja, "a IA já compila isso
  e me dá o menor deles" já é exatamente o que este mecanismo faz hoje,
  só que nunca é lido por nenhum endpoint webapp.
- **As duas buscas que o pedido descreve já existem**, dentro de
  `_collect_candidates` (`historical_bootstrap/service.py:296-305`):
  uma busca específica no site `hardwarebarato.com` (só para categorias
  `gpu/cpu/motherboard/psu/ram`) e uma busca genérica
  (`"{label}" histórico de preço menor preço`) — exatamente "tanto pelo
  hardware barato quanto na internet em si". IA entra como fallback
  quando o parsing determinístico (`_parse_candidate`, regex sobre o
  snippet) não resolve sozinho: `_interpret_ambiguous`
  (`historical_bootstrap/service.py:220-274`) faz uma chamada real de
  IA com instrução anti-alucinação explícita ("Extraia somente fatos
  explícitos... não infira nem invente").

## Correção de premissa (confirmada no código)

O pedido presumia "o preço histórico só vai vir pra produtos de
missões novas". Não é bem isso: `run_historical_bootstrap` é chamado em
`orchestration.py:2349-2377`, dentro da Fase B, para **qualquer**
oferta `MATCH` de uma missão que está **ativamente coletando** (não
precisa ser missão nova) — só pula quando `internal_history_is
_sufficient` já é verdade (30 dias de cobertura + 2 lojas) ou quando já
foi revalidado dentro da janela de `historical_bootstrap_revalidation_
days` (default 90). A limitação real é outra: **produtos de missões
pausadas, encerradas ou sem coleta agendada nunca disparam o F1**,
porque ele só roda como efeito colateral de uma coleta acontecer — não
existe hoje nenhum disparo manual/sob demanda. É exatamente essa
lacuna que o botão pedido resolve.

Também confirmado: `Product` (`backend/app/products/models.py:60-94`)
não tem nenhum campo de preço próprio hoje — todo dado de preço vive em
`PriceObservation`/`ExternalPriceReference`/`HistoricalBootstrap`. Os
dois valores pedidos podem ser calculados em tempo de leitura
reaproveitando as duas funções acima, sem necessariamente precisar de
coluna nova em `Product` — decisão de implementação, ver "Escopo"
abaixo.

## Nomenclatura (pedido do usuário: "troca a palavra por uma melhor")

Proposta: **"preço histórico registrado no GG"** (interno) e
**"preço histórico de referência"** (externo — reaproveita o nome já
usado no código/modelo, `ExternalPriceReference`, em vez de "externo"
que não diz nada pro usuário final). Sujeito a revisão do usuário ao
ler este documento.

## Objetivo

1. Dois campos visíveis para **todos os usuários** (produto/oferta,
   local exato a decidir na implementação): preço histórico registrado
   no GG (`get_internal_historical_best`) e preço histórico de
   referência (`get_external_price_reference_evidence`) — quando
   `None`, o campo simplesmente não aparece (nunca inventa valor).
2. Botão **"Buscar preço histórico"**, visível **somente para DEV**
   (mesmo padrão `Permission.DEV_PANEL_ACCESS`/`require_dev_web_session`
   já usado na TASK-126), que dispara `run_historical_bootstrap` para
   aquele produto especificamente, **fora do fluxo normal de coleta**
   — funciona mesmo para produtos de missões pausadas/encerradas, sem
   depender de uma coleta acontecer.

## Escopo — decisão do usuário sobre o botão (2026-09-21)

**Os dois campos exibem dado que já existe no banco HOJE, sem precisar
de nenhum clique** — lição explícita da TASK-123 reaplicada aqui pelo
próprio usuário ("não adianta só por o campo, vai que tem item já com
o preço e você não trouxe"): a tela lê direto
`get_internal_historical_best`/`get_external_price_reference_evidence`
(as duas já refletem `PriceObservation`/`ExternalPriceReference`
existentes) — nunca um campo que só populate depois de alguém apertar
o botão manual.

**Regra de acesso do botão "Buscar preço histórico"**:

- Produto **sem** preço histórico de referência ainda
  (`get_external_price_reference_evidence` devolve `None` — nunca
  buscado ou toda tentativa falhou): botão disponível para **qualquer
  usuário**, USER normal incluso — clique dispara `run_historical_
  bootstrap` na hora, sem confirmação extra.
- Produto **com** preço histórico de referência já dentro da janela de
  revalidação (`historical_bootstrap_revalidation_days`, default 90
  dias): USER normal **não pode** buscar de novo — botão bloqueado
  para esse caso. Só **DEV** vê a opção de forçar; ao clicar, recebe um
  aviso explícito ("o preço ainda está dentro do limite de 90 dias")
  com duas opções — **"Pesquisar mesmo assim"** ou **"Cancelar"** —
  nunca busca de novo silenciosamente.

**Endpoint**: `POST` DEV/USER (a checagem de role acontece por CASO,
não pelo endpoint inteiro — ver regra acima) que chama
`run_historical_bootstrap` diretamente para o `product_id`, não o
padrão do precedente `POST /admin/collections/trigger`
(`backend/app/webapp/admin_router.py:718-760`, que só força a próxima
coleta agendada via `MissionSource.next_run_at` — não serve aqui,
porque não funciona para missão pausada/encerrada). Confirmação
explícita (`payload.confirmation`) só é exigida no caminho DEV
"forçar mesmo dentro do limite" — o caminho USER (produto sem preço
ainda) dispara direto, sem confirmação, já que não há nada a proteger.

## Ainda em aberto na implementação

- Onde exatamente os dois campos aparecem no frontend (`frontend/`) —
  card de produto, detalhe de oferta, painel DEV.

## Fora de escopo

Mudar a lógica de busca/parsing/matching do F1 em si
(`_collect_candidates`/`_parse_candidate`/`_interpret_ambiguous`) — já
funciona, reaproveitada como está. TASK-125 (cupom no gráfico de
histórico) — tarefa separada, sem relação direta com preço histórico
externo/interno.

## Validação futura

Confirmar visualmente que os dois campos aparecem (ou somem
corretamente quando `None`) para um produto qualquer, e que o botão
DEV, usado num produto de uma missão já encerrada, dispara uma busca
real (hardwarebarato.com + busca genérica) e atualiza o campo "preço
histórico de referência" sem precisar de nenhuma coleta nova
acontecer.
