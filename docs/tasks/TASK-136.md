# TASK-136 — Funil antes da IA: só os melhores candidatos de cada pesquisa chegam à IA

Status: **Registrada (2026-10-01), planejada, NÃO iniciada.** Pedido do usuário em
conversa. Nenhum código escrito. Decisões abaixo são do usuário; os pontos marcados
"a confirmar" ficam para o início da implementação (regra do projeto: parar e relatar
antes de codar se algo divergir).

## Problema

Hoje cada loja devolve até 20 anúncios por pesquisa (`max_offers`, na ordem em que a
própria loja mostra; não foi conferido se algum provedor pede ordenação), então uma
pesquisa pode trazer até ~120 anúncios com 6 lojas. Todos são registrados e a IA decide
depois quais combinam com a missão e dá nome/identidade a eles. O custo de IA cresce com
o número de anúncios que entram, não com o número que o usuário precisa ver (os 5 mais
baratos). Em placas-mãe e principalmente RAM ("infinita" por código de peça) a lista
do GG web enche de itens. A cada nova pesquisa entram anúncios novos e os antigos
continuam guardados.

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

## Funil proposto

1. **Triagem determinística sem IA:** o título precisa conter o que a missão pede (ex.:
   `B550M`), sem kit/combo/usado/acessório, com preço e disponibilidade válidos.
2. **Agrupar por produto só para decidir** (sem criar identidade nova no banco, para não
   duplicar produtos): placas iguais de lojas diferentes viram um grupo, fica o menor
   preço. Para **RAM**, agrupar por **característica** (tipo DDR4/DDR5, capacidade,
   velocidade, marca) e não por código de peça.
3. **Ranquear pela popularidade** (vendas; senão avaliações e nota) e considerar até 10.
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
