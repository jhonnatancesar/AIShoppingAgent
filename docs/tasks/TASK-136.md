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

1. **Critério de "recomendado" (base):** a **popularidade do anúncio**, usando **todos**
   os sinais disponíveis **em conjunto**: **quantidade de vendas**, **quantidade de
   avaliações** e **nota**, mais os **filtros da própria loja na pesquisa** (mais
   avaliados, mais procurados, mais relevantes etc.) para trazer os melhores primeiro.
   Todos valem sempre; os métodos diferentes existem porque nem todo site informa todos
   os sinais (ex.: nem toda loja mostra vendas), então cada loja usa os que tiver.
   Lista curada de modelos recomendados fica para depois.
2. **Tamanho:** a pesquisa pode considerar **até 10** candidatos, mas **para a IA vão
   poucos: 2, ou até 1, conforme as avaliações** (10 para a IA é demais). Direção do
   usuário: "pegar somente os 2 mais baratos mesmo e mandar para a IA".
3. **O que fica de fora da IA continua registrado e com preço gravado:** o GG segue
   guardando os valores desses itens; só o que vai para a IA é rigidamente filtrado.
   Nada precisa ser escondido ou apagado ("se uma hora achar esse item, ele já está
   cadastrado"). Se achar um modelo novo mais barato, o ponteiro da missão passa para
   ele; senão continua acompanhando o item anterior (regra de hoje, mantida).

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
5. **Mostrar os 5 menores preços** como hoje.

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

0. **Por loja ou por missão?** Hoje o corte é por loja (8 por loja). Os "2 para a IA" valem
   por loja (até 12 por pesquisa) ou no total da missão entre as lojas (só 2)? O corte global
   exige juntar as lojas antes da IA, o que a coleta atual (uma loja por vez) não faz.
1. Como mostrar no GG web um item registrado que não passou pela IA (não conta como
   "combina com a missão" até ser classificado?).
2. Peso entre vendas, nota e número de avaliações ao combiná-los (e como tratar a loja
   que não informa algum deles).
3. Agrupamento de RAM por característica: quais características e quais marcas formam
   um grupo.

## Relação com outras TASKs

TASK-133 (catálogo e identidade): este funil reduz quanto de IA a identidade precisa
gastar, porque menos anúncios chegam a ela. TASK-134 do GG: a conferência de "o que da
coleta depende de IA" alimenta esta TASK.
