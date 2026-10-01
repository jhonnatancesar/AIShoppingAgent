# TASK-136 — Funil antes da IA: só os melhores candidatos de cada pesquisa chegam à IA

Status: **Registrada (2026-10-01), planejada, NÃO iniciada.** Pedido do usuário em
conversa. Nenhum código escrito. Decisões abaixo são do usuário; os pontos marcados
"a confirmar" ficam para o início da implementação (regra do projeto: parar e relatar
antes de codar se algo divergir).

## Problema

O gasto de IA e a lista cheia de itens de placa-mãe e RAM vêm de **o que passa pelo
filtro antes da IA e de como ele escolhe**, não de a pesquisa trazer tudo.

## Como funciona hoje (conferido no código em 2026-10-01)

*Correção do que estava escrito antes neste documento:* a primeira versão dizia que uma
pesquisa traz até ~120 anúncios e todos são registrados. **Isso está errado.** Já existe
um funil (TASK-075 e TASK-094, `_select_final_candidates` em
`app/collection/orchestration.py`):

1. Cada loja devolve até 20 cards (`max_offers`, na ordem da própria loja).
2. **Antes de qualquer gravação ou IA** há um filtro determinístico: só passa quem
   contém o modelo pedido (quando a missão tem `model`) e não parece kit/PC completo.
3. Depois, `_limit_intermediate_candidates` fica com **8 por loja**, ordenados por:
   condição (novo primeiro), vendedor (a própria loja primeiro), disponibilidade e
   **preço**. **Não há nenhum sinal de popularidade**; é "os mais baratos" dentro dos
   grupos, o que traz os baratos e desconhecidos que você viu.
4. Esses até 8 por loja (até ~48 por pesquisa com 6 lojas) são gravados e seguem para a
   IA quando ainda não têm classificação ou identidade.

**Quando a IA é chamada por anúncio que sobrou:** com a identidade já reconhecida
(regras ou catálogo) e missão de produto específico ou de família, a relevância é
decidida **sem IA** (`_deterministic_product_relevance`); sem identidade, até 3
chamadas por anúncio (relevância, nome de exibição, identidade). Missão genérica pede
relevância por IA sempre que o anúncio ainda não foi classificado.

**Sinais de popularidade que a coleta já tem:** nota e número de avaliações lidos do
card nas 6 lojas (`raw_rating_average`, `raw_review_count`; em Pichau, Terabyte e Kabum
por seletores genéricos que podem vir vazios; Amazon, Mercado Livre e Magalu mais
explícitos). **Quantidade de vendas não é lida em nenhuma loja** (Amazon e Mercado Livre
mostram "comprados no mês" e "vendidos" no texto do card, que hoje só vai como
evidência). **Nenhuma loja usa ordenação na pesquisa** (todas usam a ordem padrão do
site); os parâmetros de ordenação de cada loja precisam ser conferidos ao vivo, o que
exige abrir as lojas (não feito).

## Decisões do usuário (2026-10-01)

1. **Critério de "recomendado" (popularidade), corrigido em 2026-10-01:** a base é
   **sempre a nota (estrelas) junto com a quantidade de avaliações**, em qualquer loja.
   **Quando o site informar a quantidade de vendas, ela entra por cima**, deixando a
   escolha mais assertiva. Também aproveitar os **filtros da própria loja na pesquisa**
   (mais avaliados, mais procurados, mais relevantes etc.) para trazer os melhores
   primeiro. Lista curada de modelos recomendados fica para depois.

   **Popularidade é do PRODUTO, juntando todas as lojas (decisão do usuário,
   2026-10-01).** Para o mesmo produto, o GG lê as avaliações de **todas as lojas** e as
   **mostra juntas no card** (nota e quantidade de avaliações de cada loja, e vendas
   quando houver), mesmo que a oferta mais barata seja de outra loja. A classificação usa
   uma **média entre as lojas**: nota combinada = média das notas ponderada pela
   quantidade de avaliações de cada loja; quantidade combinada = soma das avaliações;
   vendas = soma. Exemplo do usuário: 3 avaliações 5★ na Kabum e 160 avaliações 4,9 na
   Amazon dão uma nota combinada de cerca de 4,90 com 163 avaliações (as 3 da Kabum quase
   não mexem), e não "5,0 da Kabum" ganhando.

   *Ajuste proposto (a confirmar antes de codar):* além da média ponderada, um **mínimo de
   avaliações de referência** para que um produto com pouquíssimas avaliações no total não
   vença um bem avaliado por muita gente; sem avaliação nenhuma em loja nenhuma, o produto
   fica atrás dos que têm. Vendas, quando houver, entram como segundo sinal, comparadas
   dentro do mesmo conjunto de candidatos.

   *Ponto de desenho:* o ranking acontece **antes** da IA, mas juntar as lojas por produto
   normalmente precisa da identidade, que é justamente o que a IA ajuda a descobrir. Por
   isso o agrupamento antes da IA usa uma **chave determinística aproximada** (placas-mãe:
   marca + código do modelo; RAM: tipo, capacidade, velocidade e marca) que **só serve para
   somar as avaliações**, nunca cria identidade no banco. Um agrupamento errado afeta no
   máximo o ranking daquela rodada.

   *Onde aparece:* a comparação do produto entre lojas já existe no GG web
   (`UserOfferComparison`, `app/offers/query.py`); ela passa a trazer também a nota e a
   quantidade de avaliações (e vendas) de cada loja, e o card mostra a nota combinada.
2. **Tamanho:** a pesquisa pode considerar **até 10** candidatos, mas **para a IA vão
   poucos: 2, ou até 1, conforme as avaliações** (10 para a IA é demais). Direção do
   usuário: "pegar somente os 2 mais baratos mesmo e mandar para a IA".
3. **O que fica de fora da IA continua registrado e com preço gravado:** o GG segue
   guardando os valores desses itens; só o que vai para a IA é rigidamente filtrado.
   Nada precisa ser escondido ou apagado ("se uma hora achar esse item, ele já está
   cadastrado"). Se achar um modelo novo mais barato, o ponteiro da missão passa para
   ele; senão continua acompanhando o item anterior (regra de hoje, mantida).

4. **Só 2 para a IA em TODAS as lojas somadas, não 2 por loja (usuário, 2026-10-01):**
   a intenção é deixar a IA bem restrita. No **GG web** os demais itens continuam
   listados como já pesquisados, mas na **mesma aba** aparece uma **parte de destaque**
   com **somente esses 2 itens**, os mais baratos do conjunto escolhido. Motivo: a
   diferença entre esses 2 e os outros (até 8 por loja) pode ser mínima, e o usuário pode
   preferir outro por ser mais bonito, pela cor etc., então os outros não somem.

## Como fazer o corte total — DECIDIDO: opção A, barreira por ciclo (usuário, 2026-10-01)

Hoje a coleta é por **`(item de monitoramento, loja)`**, cada uma na sua agenda
(`MonitoringItemStore.next_run_at`, backoff, intervalo mínimo por loja), e o resultado
de cada coleta é distribuído às missões por fan-out (`shared_collection.py`). Não existe
a noção de "ciclo de todas as lojas". Opções que foram avaliadas:

- **A) Barreira por ciclo (ESCOLHIDA):** esperar o resultado de todas as lojas escolhidas
  pelo usuário, escolher os 2 e só então chamar a IA.
- B) Comparar com o que já está no banco: **descartada pelo usuário**, porque o banco
  pode estar desatualizado pelo tempo parado e, para um item que ainda não existe no
  banco, a pesquisa de todas as lojas escolhidas precisa rodar e coletar primeiro.
- C) Corte por loja (2 por loja): fora do que o usuário pediu.

**Consequências da opção A no desenho (a detalhar antes de codar):**

1. **Conceito de ciclo** por item de monitoramento: um ciclo abre quando a primeira loja
   daquele item coleta e fecha quando **todas as lojas habilitadas rodaram pelo menos
   uma vez**, **sem tempo limite** (decisão do usuário, 2026-10-01: as lojas não levam
   30 minutos por ciclo). **Loja bloqueada ou com erro conta como rodada, com erro**:
   o ciclo fecha com ela falhando, então não existe risco de a missão ficar parada por
   uma loja só (decisão do usuário). Loja em espera de bloqueio que nem chega a tentar
   é tratada como erro naquele ciclo.
2. **Fase A por loja continua gravando** as ofertas e os preços (Offer e PriceObservation),
   **sem IA**. Só a **escolha** e a **IA** passam a acontecer no fechamento do ciclo.
3. **Fechamento do ciclo:** junta as ofertas de todas as lojas, ranqueia por popularidade,
   fica com o conjunto de até 10 e escolhe as **2 mais baratas**. A IA (relevância, nome,
   identidade) roda **só nessas 2** e só se ainda não tiverem classificação. A Fase C
   (alertas, ponteiro da missão) roda com o resultado.
4. **Ciclos seguintes:** recalcula as 2 escolhidas; a IA só é chamada quando o par muda
   e o novo item ainda não está classificado.
5. **Durabilidade:** o estado do ciclo precisa sobreviver a crash e a mais de um processo
   de worker (mesmo cuidado do fan-out durável da TASK-112: claim atômico e retomada).
6. **Missão com várias lojas e itens compartilhados entre missões:** a escolha é por
   item de monitoramento (compartilhado), e o fan-out por missão segue como hoje.

## Tela da missão no GG web — DECIDIDO: três blocos na mesma aba (usuário, 2026-10-01)

Hoje a tela da missão ("Ofertas relevantes", `frontend/src/pages/missions/
MissionDetailPage.tsx`, alimentada por `list_current_offer_links_for_mission` em
`backend/app/offers/query.py`) lista só as ofertas da coleta mais recente de cada loja
que já foram classificadas como "combina" ou "talvez combina", ordenadas por loja e data
de visualização. Como só 2 itens passarão pela IA, o resto sumiria da lista. Decisão
(opção 1): na **mesma aba**, três blocos:

1. **Destaque:** só os 2 itens escolhidos pelo funil (os mais baratos do conjunto
   popular).
2. **Ofertas relevantes:** como hoje (classificadas).
3. **Outros resultados:** as ofertas gravadas que não passaram pela IA, **sem
   classificação**, ordenadas por preço (menor primeiro), para o usuário poder preferir
   um deles por ser mais bonito, pela cor etc.

Preço e histórico desses itens continuam sendo gravados normalmente.

## Funil proposto (a evoluir o funil que já existe)

1. **Triagem determinística sem IA** (já existe em parte: modelo e kit): o título precisa conter o que a missão pede (ex.:
   `B550M`), sem kit/combo/usado/acessório, com preço e disponibilidade válidos.
2. **Agrupar por produto só para decidir** (sem criar identidade nova no banco, para não
   duplicar produtos): placas iguais de lojas diferentes viram um grupo, fica o menor
   preço. Para **RAM**, agrupar por **característica** (tipo DDR4/DDR5, capacidade,
   velocidade, marca) e não por código de peça.
3. **Ranquear pela popularidade** (todos os sinais que a loja informar) e considerar até 10, no
   lugar de ordenar só por condição, vendedor, disponibilidade e preço como hoje.
4. **Enviar à IA só os 2 mais baratos desse conjunto** (1 quando as avaliações apontarem
   claramente um só). Os "2 mais baratos" saem do conjunto de até 10 escolhido pela
   popularidade, e não dos 2 mais baratos de toda a pesquisa (**confirmado pelo
   usuário em 2026-10-01**).
5. **Mostrar os 5 menores preços** como hoje, e acrescentar o **destaque** na mesma aba
   com só os 2 itens escolhidos (mais baratos do conjunto popular); os demais continuam
   na lista.

## Pontos para investigar antes de codar

- Para cada loja (Pichau, Terabyte, Amazon, Kabum, Magalu, Mercado Livre): quais sinais
  de popularidade o card ou a página traz (vendas, avaliações, nota) e se a pesquisa
  aceita ordenar por "mais avaliados/relevantes"; o conjunto de sinais usado por loja
  é o que ela informar.
- Quais passos da coleta hoje chamam a IA por anúncio (relevância com a missão, nome de
  exibição, identidade, pesquisa de mercado) e como cada um deve se comportar para o
  anúncio que ficou fora do funil (fica registrado e com preço, mas sem classificação).
- Missão de **família** ("B550M", muitos modelos) versus missão de **modelo exato**
  (RTX 4060 de uma marca): o funil de até 10 vale para família; modelo exato vale só
  para aquele modelo. *A confirmar.*

## Perguntas em aberto

1. Como mostrar no GG web um item registrado que não passou pela IA (não conta como
   "combina com a missão" até ser classificado?).
2. Confirmar o ajuste da popularidade (mínimo de avaliações de referência) e a chave
   de agrupamento aproximada por categoria (placa-mãe, RAM).
3. Agrupamento de RAM por característica: quais características e quais marcas formam
   um grupo.

## Relação com outras TASKs

TASK-133 (catálogo e identidade): este funil reduz quanto de IA a identidade precisa
gastar, porque menos anúncios chegam a ela. TASK-134 do GG: a conferência de "o que da
coleta depende de IA" alimenta esta TASK.
