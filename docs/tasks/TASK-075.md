# TASK-075 — Canonicalizar o produto na interpretação da missão e reduzir resultados irrelevantes antes de persistir e antes da IA

Status: **Concluída em 2026-08-11**, desenho aprovado explicitamente
pelo usuário (com três rodadas de ajuste), implementada e validada com
pipeline oficial completo, migration real em PostgreSQL descartável e
chamadas reais contra o perfil `ADMIN`.

Ajustes finais incorporados nesta revisão, antes de codar: gate
obrigatório de identidade para a regra Amazon (item 8.1), documentação
corrigida sobre missões antigas (item "Compatibilidade"), normalização
tolerante a separador no filtro de modelo (item 5, algoritmo revisado),
lista de sufixo forte revisada para `TI`/`SUPER`/`XT`/`XTX`/`GRE`
(`GRE` reincluída por decisão explícita do usuário, revertendo minha
remoção da rodada anterior), `product_type` confirmado fora de escopo.

Dependência: build em cima da TASK-074 (mesmo arquivo, `backend/app/
intent/interpreter.py`, mesmo prompt) — não reverte nada da TASK-074,
estende.

## Contexto

Resumo do histórico desta TASK (para quem ler depois): a primeira
versão tentava resolver o ruído de resultados só com um filtro de
modelo extraído por regex depois da busca. O usuário revisou duas
vezes: primeiro pra centrar a estratégia na canonicalização feita pela
própria IA que já interpreta a missão (não um filtro regex isolado);
depois, ao decidir sobre o risco de ambiguidade em variantes
(`RTX 4070` × `RTX 4070 Ti` × `RTX 4070 Ti SUPER`), escolheu persistir
o modelo completo como campo estruturado (**Opção 2**), em vez de
tentar reconstruí-lo por regex do `search_query` já montado.

## Decisão final de desenho (2026-08-11)

### 1. `IntentInterpreter` devolve `model` estruturado, na mesma chamada

`Intent.parameters` ganha um novo campo opcional `model: str | None`.
Preenchido pela mesma chamada de IA (`purpose="interpret_purchase_
intent"`) que já gera `search_query` — **sem segunda chamada**.

- `"quero um 9950x3d"` → `search_query: "Processador AMD Ryzen 9
  9950X3D"`, `model: "9950X3D"`.
- `"quero uma 4070 ti"` → `search_query`: descrição canônica correta da
  placa (`"Placa de Vídeo NVIDIA RTX 4070 Ti"`), `model: "RTX 4070 Ti"`
  — variante preservada por completo, nunca reduzida a `"4070"`.
- Sem modelo específico identificável (ex.: `"notebook gamer"`,
  `"mouse bom e barato"`) → `model: null`. Não inventa.

### 2. Migration — `mission_criteria.model`

**Tabela confirmada como a certa** (ver auditoria abaixo) — não há
alternativa estruturalmente melhor no modelo atual.

```python
"""Adiciona modelo estruturado do produto ao critério da missão (TASK-075).

Revision ID: 20260811_0001
Revises: 20260810_0001
Create Date: 2026-08-11
"""


def upgrade() -> None:
    op.add_column(
        "mission_criteria",
        sa.Column("model", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_mission_criteria_model_not_blank",
        "mission_criteria",
        "model IS NULL OR btrim(model) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mission_criteria_model_not_blank", "mission_criteria", type_="check"
    )
    op.drop_column("mission_criteria", "model")
```

- **Nullable, sem `server_default`** — `ADD COLUMN` nullable sem
  default é metadata-only no PostgreSQL usado pelo projeto (18.x), sem
  reescrever a tabela, sem lock longo, sem backfill.
- **Nenhuma missão existente é tocada** — todas ganham `model = NULL`
  automaticamente, continuam válidas; o filtro determinístico (item 4)
  já trata `NULL` como "não descartar" por design, então nenhuma missão
  antiga muda de comportamento.
- **`String(64)`**: generoso para qualquer variante real (`"RTX 4070 Ti
  SUPER"` tem 18 caracteres); consistente com o padrão de colunas
  curtas já usado no projeto.
- **`CHECK` de não-branco**: mesmo padrão já usado em `search_query`
  (`ck_mission_criteria_search_query_not_blank`) — `NULL` é válido,
  string vazia não.
- **Downgrade**: remove constraint e coluna, sem efeito colateral em
  nenhum outro campo — nenhuma perda de dado que não seja o próprio
  `model` (que deixa de existir, como esperado num downgrade).

### 3. Contrato do `IntentInterpreter`

`backend/app/intent/interpreter.py`:

- `_ALLOWED_PARAMETER_KEYS` ganha `"model"`.
- `_parse_parameters` ganha `model = _optional_str(raw.get("model"))`.
- `_SYSTEM_PROMPT`: parágrafo do JSON esperado ganha `"model": string ou
  null`; parágrafo de correção (já existente da TASK-074) estendido
  para explicar canonicalização completa (tipo primeiro) e a
  preservação de variante; novos exemplos few-shot cobrindo `9950x3d` →
  `model: "9950X3D"` e `4070 ti` → `model: "RTX 4070 Ti"`.

`backend/app/intent/contracts.py` (`IntentParameters`):

```python
model: str | None = None
"""TASK-075: modelo/variante completo do produto, quando identificável
com segurança pela mesma interpretação que já gera search_query — nunca
reduzido (preserva Ti/SUPER/XT/XTX etc.). None quando não há modelo
específico."""
```

Validação em `__post_init__`, mesmo padrão de `search_query`: não pode
ser string vazia quando informado.

O comentário de classe de `IntentParameters` ("nenhum campo novo de
domínio é introduzido por este contrato") precisa de um ajuste de uma
linha reconhecendo que `model` é o primeiro campo novo, introduzido
junto com a coluna equivalente em `MissionCriteria` nesta TASK — só
documentação, não muda a regra de fundo (o campo continua espelhando
`MissionCriteria`, só que agora `MissionCriteria` também ganhou o
campo).

### 4. Encadeamento até a persistência — todos os pontos exatos

`model` precisa atravessar o fluxo de confirmação em dois caminhos
possíveis (com ou sem pergunta de lojas no meio, TASK-070) antes de
chegar em `create_mission_from_criteria`:

1. `app/telegram/router.py:648` (`_stage_create_mission`) — lê
   `intent.parameters.model`.
2. Caminho direto (lojas já informadas): `router.py:666-671` chama
   `stage_create_mission(model=intent.parameters.model, ...)`.
3. Caminho indireto (sem loja, TASK-070): `router.py:~660`
   (`stage_await_create_mission_sources`) precisa passar `model` pro
   payload intermediário; `router.py:610-615`
   (`_advance_pending_create_mission_sources`-equivalente) lê
   `payload["model"]` e repassa pra `stage_create_mission`.
4. `app/telegram/confirmation.py:121` (`stage_create_mission`) e `:231`
   (`stage_await_create_mission_sources`) — cada um ganha o parâmetro
   `model: str | None` e a chave `"model"` no dict devolvido.
5. `router.py:1017-1027` — chamada de `create_mission_from_criteria`
   ganha `model=payload.get("model")`.
6. `app/missions/service.py::create_mission_from_criteria` — novo
   parâmetro `model: str | None = None`; `MissionCriteria(model=model,
   ...)` na criação.
7. `app/missions/models.py::MissionCriteria` — novo campo `model:
   Mapped[str | None] = mapped_column(String(64), nullable=True)`.

**Não precisa mudar**: `ClaimedCollection`
(`orchestration.py:96-102`) nem a query que monta claims — o filtro
determinístico roda dentro de `_persist_success`, que já busca
`MissionCriteria` inteiro do banco (linha 407-409); `criteria.model`
fica disponível ali de graça, sem precisar passar por mais nenhuma
camada.

`describe_create_mission` (mensagem de confirmação) **não muda** —
continua mostrando só `search_query`, que já inclui o modelo como parte
do texto canônico; mostrar `model` separado seria redundante.

### 5. Filtro determinístico de modelo — algoritmo exato (revisado: separador tolerante)

Duas checagens, aplicadas só quando `criteria.model` não é `NULL` — e
só então os sobreviventes contam como identidade confirmada (ver gate
do item 8.1). O usuário pediu explicitamente que a comparação tolere
diferença de formatação/separador (`"RTX 4070 Ti"` == `"RTX4070Ti"` ==
`"RTX-4070-Ti"`) sem virar substring permissiva — as duas exigências
juntas (tolerante a formato, mas ainda distingue `RTX 4070` de `RTX
4070 Ti`) são resolvidas com o mesmo mecanismo:

**a) Casamento por padrão, com separador flexível e limite alfanumérico
nas duas pontas** (resolve `9950X3D` × `9950X3D2`, `A9950X3D`, **e**
tolerância de separador ao mesmo tempo): normaliza `model` e o título
(maiúsculo, sem acento). Constrói um padrão a partir de `model` onde
cada espaço interno vira "zero ou mais separadores" (espaço, hífen,
underscore) — assim `"RTX 4070 TI"` casa com `"RTX 4070 TI"`, `"RTX-
4070-TI"` e `"RTX4070TI"` igualmente, sem preciso de três regras
diferentes. O padrão inteiro só é aceito se **não** houver caractere
alfanumérico imediatamente antes nem imediatamente depois do trecho
casado no título (limite de palavra "de verdade", não just um espaço
opcional). `9950X3D2` falha porque o `2` fica colado depois do trecho
casado, sem separador nenhum ali — `A9950X3D` falha pelo mesmo motivo
do lado esquerdo.

**b) Checagem de sufixo de variante** (resolve `RTX 4070` × `RTX 4070
Ti` × `RTX 4070 Ti SUPER`, que a checagem "a" sozinha não resolve — um
espaço depois do trecho casado não é "colado", então "a" aceitaria os
dois): depois de casar o modelo no título (checagem "a"), pega a
próxima palavra do título (ignorando separadores entre elas). Se essa
palavra pertencer à lista de sufixos fortes abaixo **e** não já fizer
parte do `model` da missão, rejeita.

**Lista de sufixo forte** (decisão final do usuário, nesta rodada):
`TI`, `SUPER`, `XT`, `XTX`, `GRE`. São sufixos oficiais de nomenclatura
de fabricante (NVIDIA: Ti/Super; AMD: XT/XTX/GRE) — sempre indicam
SKU/chip realmente diferente. **Não entram**: `OC` (edição de vendedor/
overclock de fábrica, mesmo chip), `PRO`/`PLUS`/`MAX` (uso inconsistente
entre categorias, alto risco de falso-positivo).

| `model` da missão | título do candidato | resultado |
| --- | --- | --- |
| `RTX 4070` | `RTX 4070` | aceita |
| `RTX 4070` | `RTX 4070 Ti` | rejeita (`b`, `TI` é sufixo forte) |
| `RTX 4070` | `RTX 4070 SUPER` | rejeita (`b`, `SUPER` é sufixo forte) |
| `RTX 4070 Ti` | `RTX 4070 Ti` | aceita |
| `RTX 4070 Ti` | `RTX 4070 Ti SUPER` | rejeita (`b`, `SUPER` não pedido) |
| `RTX 4070 Ti` | `RTX 4070 Ti OC` | **aceita** (`OC` fora da lista) |
| `RTX 4070 Ti` | `RTX4070Ti` | aceita (`a`, separador tolerante) |
| `RTX 4070 Ti` | `RTX-4070-Ti` | aceita (`a`, separador tolerante) |
| `9950X3D` | `9950X3D2` | rejeita (`a`, colado, nem chega em `b`) |
| `9950X3D` | `A9950X3D` | rejeita (`a`, colado do outro lado) |

Sem `criteria.model` (`NULL`): nenhuma das duas checagens roda — regra
conservadora mantida, candidato segue pro fluxo de relevância existente
**e nunca é tratado como identidade confirmada** (ver gate do item
8.1).

### 6. Filtro de bundle/PC completo — inalterado da versão anterior

Lista fixa de palavras-sinal de sistema completo/kit (`computador`, `pc
gamer`, `workstation`, `kit`, `combo`, `notebook` quando a missão não é
notebook). Ausente no `search_query` da missão, presente no título do
candidato → rejeita. Independente do filtro de modelo — roda mesmo
quando `model` é `NULL`, porque não depende dele.

### 7. Kabum — mantido, sem mudança

`facet_filters=eyJrYWJ1bV9wcm9kdWN0IjpbInRydWUiXX0=` em `KabumProvider.
build_url`. Já validado ao vivo em rodada anterior desta TASK.

### 8. Amazon — regra exclusiva, identidade resolvida pelos filtros 5+6, DEPOIS de eles rodarem uma única vez

Só Amazon (`claim.source_code == "amazon"`). Pichau/Terabyte/Kabum não
recebem esta regra — varejo direto, sem revenda.

#### 8.1. Gate obrigatório — identidade forte é pré-requisito

**`_select_amazon_lowest_price` só pode rodar quando `criteria.model is
not None`.** Quando `criteria.model` é `NULL` (missão sem modelo
específico identificável — inclui **toda missão antiga**, criada antes
desta TASK, e buscas genéricas como `"notebook gamer"`, `"mouse sem
fio"`, `"processador AMD"`):

- a seleção por menor preço **não roda**;
- os sobreviventes do filtro de bundle (item 6, que roda sempre,
  independente de `model`) **não são tratados como "o mesmo produto"**
  entre si — cada um segue como candidato independente;
- o fluxo existente de persistência + `classify_offer_relevance`
  continua exatamente como antes desta TASK, sem seleção por preço
  nenhuma.

Nunca escolher "o item mais barato da Amazon" quando não existe modelo
específico estabelecendo identidade — isso arriscaria comparar
produtos genuinamente diferentes (ex.: dois notebooks gamer distintos)
como se fossem o mesmo item. `model is not None` é exatamente a mesma
condição que já faz o filtro de modelo (item 5) rodar de verdade nos
candidatos — então "sobreviveu ao filtro de modelo" e "identidade forte
o bastante pra comparação de preço" são a mesma coisa por construção.

**Ordem corrigida** (pedido explícito do usuário — os filtros rodam
**uma única vez**, produzindo uma coleção de sobreviventes; só essa
coleção entra na seleção da Amazon; só o resultado final entra no loop
de persistência — nunca "filtra, seleciona Amazon, filtra de novo
dentro do loop"):

```
normalized.offers
  → _filter_deterministic_candidates(criteria, offers)   [passo único: item 5 + item 6]
  → sobreviventes
  → SE source_code == "amazon": _select_amazon_lowest_price(sobreviventes)
  → SENÃO: sobreviventes, sem mudança
  → candidatos finais
  → loop de persistência atual (dedup, _resolve_offer, PriceObservation, classify_offer_relevance)
```

Candidatos que sobrevivem ao passo único (modelo + bundle, item 5+6)
são tratados como "o produto pedido", **independente do ASIN** —
resolve definitivamente o problema de ASINs diferentes pro mesmo
produto (confirmado ao vivo: `B0DVZSG8D5` R$ 3.999, `B0GLRTTRCK`
R$ 5.226, `B0H2KX1S7S` R$ 6.723, `B0H2L3FQX9` R$ 10.674 — todos
"9950X3D" depois de passar no filtro). Entre os sobreviventes,
`_select_amazon_lowest_price` mantém só o(s) de menor `amount`;
descarta o resto antes de qualquer persistência.

**Empate no menor preço — achado novo que muda a recomendação
anterior**: como `seller_name`/`seller_external_id` não são extraídos
hoje (confirmado ao vivo, seletor `[aria-label^="Vendido por"]` não
bateu em nenhum dos 53 resultados testados), o desempate é
determinístico e estável — ordena por `external_id` (ASIN) crescente,
usa o primeiro.

Na rodada anterior deste plano eu tinha recomendado persistir **todos**
os empatados "para histórico". Reli `_latest_match_observations_by_
store` (`orchestration.py:816-836`, a função que a pré-lista usa pra
escolher a candidata de cada loja) e achei um problema: ela só
considera um `PriceObservation` se já existir um `MissionOfferRelevance`
com `classification == MATCH` pra aquele `(mission, offer)` — ou seja,
**cada oferta empatada persistida ainda precisaria da sua própria
chamada de `classify_offer_relevance`** pra ter chance de aparecer na
pré-lista. Persistir 4 empatados e classificar todos os 4 é exatamente
o tipo de chamada de IA redundante que esta TASK existe pra eliminar —
o histórico de 4 preços idênticos, no mesmo instante, de vendedores
diferentes não tem valor prático que justifique 4 chamadas de IA.

**Recomendação revisada**: persistir e classificar **só o vencedor do
desempate** (`external_id` ordenado, o primeiro) — os demais empatados
não geram `Offer`/`PriceObservation`/chamada de relevância, do mesmo
jeito que os candidatos de preço mais alto. Isso mantém a redução de
chamadas de IA coerente (1 candidata por loja entrando em
`classify_offer_relevance`, inclusive na Amazon) sem quebrar
`_latest_match_observations_by_store`, que já espera exatamente "uma
candidata por loja".

Preciso que você confirme essa mudança antes de eu seguir — é uma
correção sobre o que eu tinha recomendado antes, não sobre o que você
pediu originalmente (você pediu "todos os empatados podem ser
persistidos"; a descoberta de como a pré-lista realmente escolhe a
candidata por loja é que me fez reconsiderar).

Preferência por "Vendido por Amazon.com.br" fica registrada como
melhoria futura (exigiria descobrir se a informação só existe na
página do produto, o que muda o custo de scraping — fora do escopo
desta TASK, sem aumentar chamadas externas).

### 9. Frete, pré-lista, IA — inalterado

Sempre `amount`, nunca `total_amount`, frete fora da decisão. TASK-068
(pré-lista) intocada — a seleção interna de vendedores da Amazon
resolve "qual oferta representa a Amazon nesta rodada", antes e
separado de "quais lojas entram na pré-lista". Nenhum provider de IA
novo, nenhuma chamada extra, nenhum prompt de relevância maior — os
filtros 5/6/8 são funções Python puras, sem `await`.

### 10. `product_type` estruturado — analisei e recomendo NÃO adicionar

Você pediu pra eu avaliar se vale persistir `product_type` (ex.:
`"processador"`) na mesma migration, pra tornar o filtro de PC
completo/kit mais seguro — e explicar se concluir que é redundante.

**Conclusão: é redundante para o que o filtro precisa fazer, pelos
seguintes motivos:**

1. A busca canônica (item 1) já é desenhada pra começar pelo tipo
   (`"Processador AMD Ryzen 9 9950X3D"`) — o tipo já está presente no
   início do `search_query`, na posição que o filtro de bundle (item 6)
   já usa hoje.
2. O filtro de bundle (item 6) já resolve exatamente o exemplo que
   você deu (`Processador` × `"Computador Gamer... 9950X3D..."`) **sem
   precisar saber o valor do tipo** — ele só verifica se uma
   palavra-sinal de sistema completo (`computador`, `kit`, `combo`...)
   está ausente no `search_query` e presente no título. Isso já é
   verdade hoje: `"Processador AMD Ryzen 9 9950X3D"` não contém
   `"computador"`, o título contém — rejeita, sem precisar de
   `product_type` nenhum.
3. **O ponto decisivo**: ter `product_type = "processador"` como campo
   estruturado não elimina a necessidade de um dicionário de
   palavras-sinal — pra saber se um título é "na verdade uma placa-mãe"
   (um tipo diferente que não é bundle nem contém sufixo de variante),
   eu ainda precisaria de uma lista de palavras que sinalizam cada
   tipo incompatível, comparada contra `product_type`. É o mesmo
   trabalho de manutenção de lista, só reorganizado — não fica mais
   simples nem mais seguro só por o valor "tipo" estar num campo
   separado.
4. Existe um caso de borda genuíno que nem o filtro de bundle nem
   `product_type` resolveriam sozinhos: um título de placa-mãe que
   menciona "RTX 4070" na descrição (compatibilidade), pra uma missão
   de placa de vídeo. Isso escaparia dos dois desenhos igualmente — e
   já é coberto pela rede de segurança existente (regra conservadora:
   quando o filtro determinístico não tem certeza, o candidato segue
   pra `classify_offer_relevance`, que já resolveria isso
   semanticamente hoje). Adicionar `product_type` não fecha esse caso
   de borda; só o filtro de relevância por IA fecha.

**Custo de adicionar mesmo assim**: mais uma coluna na migration, mais
um campo na saída da IA pra manter consistente com `search_query`
(risco de os dois discordarem entre si em algum caso), sem redução
correspondente de chamadas de IA nem de candidatos rejeitados além do
que o filtro de bundle já cobre.

**Recomendação: manter só `model` na migration.** Se no futuro
aparecer um padrão real de tipo incompatível que o filtro de bundle
não cobre (o caso da placa-mãe acima, por exemplo), registro como
melhoria separada — nesse momento aí sim `product_type` teria um
motivo concreto, não hipotético.

## Auditoria — `mission_criteria` é o lugar certo?

Revisei o modelo procurando alternativa estruturalmente melhor. Opções
consideradas e descartadas:

- **`missions` (tabela mãe)**: `Mission` já tem campos de estado de
  ciclo de vida (`status`, `state_version`) e da pré-lista (TASK-068);
  `model` é conceitualmente um critério de busca, não estado da missão
  — colocar lá misturaria responsabilidades sem ganho real.
- **Tabela nova dedicada**: desproporcional para 1 campo nullable que
  vive e morre junto com `search_query` (mesmo dono, mesma criação,
  nunca editado independentemente — o fluxo de edição de missão,
  TASK-069/071, nunca toca `search_query` nem tocaria `model`).
- **`Product`/`Offer` (lado da coleta)**: errado por natureza — essas
  tabelas representam o que foi *encontrado* em cada loja, não o que a
  missão está *procurando*. `model` é entrada da missão, não saída da
  coleta.

**Conclusão: `mission_criteria`, ao lado de `search_query`, é a opção
certa** — mesmo ciclo de vida, mesma origem (uma única chamada de IA na
criação), mesmo dono conceitual.

## Ordem exata do pipeline (ponta a ponta, corrigida)

```
mensagem do usuário
  → IntentInterpreter.interpret (1 chamada de IA)
      → search_query canônico + model (estruturado ou null)
  → confirmação (stage_create_mission / stage_await_create_mission_sources)
  → create_mission_from_criteria → MissionCriteria.search_query + .model
  → [tempo depois, processo separado: collection_worker]
  → providers fazem a busca com search_query canônico (Kabum já com facet_filters)
  → _persist_success (orchestration.py):
      1. busca criteria (já existe hoje, linha 407-409)
      2. survivors = _filter_deterministic_candidates(criteria, normalized.offers)
         — PASSO ÚNICO: aplica filtro de modelo (item 5, só se criteria.model
         existir) + filtro de bundle (item 6, sempre) em cada item de
         normalized.offers exatamente uma vez; devolve só quem passou nos dois.
      3. final_offers = (
             _select_amazon_lowest_price(survivors)
             if claim.source_code == "amazon" and criteria.model is not None
             else survivors
         )
         — só entra aqui quem já sobreviveu ao passo 2; nunca reaplica
         modelo/bundle dentro desta função. Gate do item 8.1: sem
         `criteria.model`, a Amazon também cai no "else survivors" —
         nunca escolhe "mais barato" sem identidade confirmada.
      4. for item in final_offers:  (loop de persistência atual, sem mudança
         de estrutura interna)
             a. dedup por identity_key (já existe)
             b. _resolve_offer (já existe)
             c. PriceObservation (já existe)
             d. classify_offer_relevance (já existe)
      5. candidato rejeitado no passo 2 ou descartado no passo 3 nunca chega
         ao passo 4 — nunca cria Product/Offer/PriceObservation, nunca chama
         classify_offer_relevance.
  → _evaluate_mission_prelist (inalterado)
```

Duas funções puras novas, cada uma chamada **uma vez** por execução de
`_persist_success`, nesta ordem — não há mais nenhuma reavaliação de
modelo/bundle depois que `_filter_deterministic_candidates` já rodou.

## Compatibilidade com missões antigas

**Correção**: não é "comportamento exatamente igual ao atual" — é uma
nova filtragem determinística conservadora que passa a valer também
pra missões antigas.

Toda missão criada antes do deploy tem `model = NULL` (coluna nova,
sem backfill). Nessas missões:

- **filtro de modelo (item 5)**: não roda de verdade (sem `model` pra
  comparar) — nenhuma rejeição por modelo, igual a hoje.
- **seleção de menor preço da Amazon (item 8.1)**: não roda — gate
  `model is not None` não é satisfeito. Amazon continua com o
  comportamento de hoje pra essas missões (todos os candidatos que
  passarem no filtro de bundle seguem individualmente pro fluxo
  normal, sem escolha de "mais barato").
- **filtro de bundle (item 6)**: **roda igual, porque não depende de
  `model`** — uma missão antiga de `"processador AMD"` (sem modelo
  específico) passa a rejeitar deterministicamente um resultado
  `"Computador Gamer AMD..."`, o que **não acontecia antes desta TASK**.
  Isso é uma mudança de comportamento real (uma redução de ruído a
  mais), não "igual a hoje" — só não é uma regressão, porque a regra é
  conservadora (só rejeita sinal de bundle inequívoco) e vale só para
  coletas novas, nunca reavalia nem apaga o que já foi coletado antes.

## Testes previstos

**Canonicalização** (contra IA real, perfil `ADMIN`, nunca `USER`):
`9950x3d` → `search_query` completo + `model: "9950X3D"`; `4070 ti` →
`model: "RTX 4070 Ti"` (variante preservada, nunca virar `"4070"`);
pedido sem modelo específico → `model: null`; pedido já completo não
degrada.

**Migration**: upgrade aplica coluna nullable sem erro em banco com
missões existentes; downgrade remove sem afetar outras colunas;
`alembic upgrade head` + `downgrade -1` + `upgrade head` round-trip
(mesmo padrão já usado nas migrations anteriores do projeto).

**Filtro de modelo**: `9950X3D` aceita `9950X3D`; rejeita `9950X3D2`;
rejeita prefixo/sufixo colado; case-insensitive; `RTX 4070` aceita `RTX
4070`; rejeita `RTX 4070 Ti`; rejeita `RTX 4070 SUPER`; `RTX 4070 Ti`
aceita `RTX 4070 Ti`; rejeita `RTX 4070 Ti SUPER`; **aceita** `RTX 4070
Ti OC` (`OC` não está na lista revisada — não rejeita por incerteza);
`model = NULL` não descarta nada.

**Filtro de bundle**: computador completo contendo o modelo não passa
quando a missão é o componente avulso; candidato ambíguo não é
descartado agressivamente.

**Passo único / ordem**: `_filter_deterministic_candidates` chamada
exatamente uma vez por execução (teste com contador/mock de chamadas,
não só resultado); `_select_amazon_lowest_price` só recebe candidatos
já filtrados — teste garante que ela nunca reavalia modelo/bundle
internamente.

**Amazon**: 4 preços diferentes do "mesmo" produto (confirmados por
modelo+bundle, ASINs diferentes) → só o menor sobrevive; empate no
mínimo resolvido deterministicamente por ASIN, **só o vencedor do
desempate persiste e chama `classify_offer_relevance`** — os demais
empatados não geram `Offer`/`PriceObservation` nem chamada de IA;
anúncio mais caro nunca persistido nem chama relevância. **Gate**:
missão com `model = NULL` na Amazon → seleção de menor preço nunca
roda, mesmo com múltiplos candidatos sobrevivendo ao filtro de bundle
— cada um segue independente pro fluxo normal (teste dedicado
confirmando que nenhum item é descartado por preço nesse caso).

**Normalização tolerante a separador**: `model = "RTX 4070 Ti"` aceita
título com `"RTX 4070 Ti"`, `"RTX-4070-Ti"` e `"RTX4070Ti"`
igualmente; continua rejeitando `"RTX 4070 Ti SUPER"` nos três estilos
de separador.

**Regressão**: Pichau/Terabyte/Kabum não recebem a regra de menor preço
da Amazon; pré-lista, alertas, `CollectionRun` e eventos inalterados;
missão antiga (`model = NULL`) segue funcionando exatamente como hoje.

## Riscos

- **Lista de sufixos de variante incompleta**: mitigado pela regra
  conservadora — sufixo desconhecido não rejeita, só os sufixos
  cadastrados na lista fixa disparam a checagem.
- **Lista de palavras-bundle incompleta**: mesmo mitigador.
- **Canonicalização inconsistente entre execuções de IA**: mesmo risco
  que qualquer resposta de IA hoje — mitigado por validação manual real
  antes de publicar, igual às TASKs 073/074.
- **Migration**: risco mínimo — coluna nullable sem default, sem
  reescrita de tabela, downgrade limpo testado no pipeline oficial
  (`scripts\check.ps1` já roda round-trip de migration em PostgreSQL
  descartável).

## Rollback

Reverter os commits (checkout da tag anterior) + `alembic downgrade`
até a revisão anterior a `20260811_0001`. Nenhuma missão existente é
afetada (a coluna nova só é lida, nunca obrigatória); histórico de
`PriceObservation` continua íntegro.

## Impacto esperado

- **Ofertas persistidas**: mesma estimativa da versão anterior do
  plano — de dezenas de candidatos por loja pra poucas unidades, com a
  Amazon provavelmente a única com mais de 1 registro por missão
  (empate no menor preço). O modelo estruturado torna essa redução mais
  confiável que a versão baseada em regex, principalmente em categorias
  com variantes por sufixo (GPUs).
- **Chamadas de IA**: proporcional — cada oferta rejeitada
  deterministicamente é uma chamada a menos de `classify_offer_
  relevance` (e, na primeira aparição de um `Product`/`Offer`, uma
  chamada a menos de normalização de título). Com a recomendação
  revisada do item 8 (só o vencedor do desempate da Amazon é
  classificado), o limite passa a ser **no máximo 1 chamada de
  relevância por loja por missão por rodada de coleta** — mesmo quando
  a Amazon tem múltiplos vendedores empatados no menor preço. Ataca
  diretamente o estouro de cota observado ao vivo nesta sessão.

## Fora do escopo desta TASK

- Corrigir extração de vendedor da Amazon (registrado como melhoria
  futura).
- Qualquer mudança na pré-lista (TASK-068), em `evaluate_price_alerts`
  ou na semântica MATCH/POSSIBLE_MATCH/NO_MATCH (TASK-063).
- Mostrar `model` na mensagem de confirmação (`describe_create_
  mission`) — `search_query` já basta, não é redundância necessária.

## Validação final (2026-08-11)

- **Pipeline oficial completo** (`scripts\check.ps1`): Gitleaks, lint,
  formatação, **910 testes (91,17% cobertura)**, migration head
  `20260811_0001`, **21 testes de integração PostgreSQL reais** — todos
  aprovados (`Pipeline local aprovado.`). A migration foi de fato
  aplicada contra um PostgreSQL 18.4 descartável como parte da suíte de
  integração, confirmando que o `upgrade()` funciona em banco real.
- **Testes determinísticos novos**: `_title_matches_model` (tabela
  completa dos casos deste documento, incluindo separador tolerante e
  sufixo de variante), `_title_looks_like_bundle`, `_filter_
  deterministic_candidates`, `_select_amazon_lowest_price` (menor
  preço, desempate por ASIN, gate ausente de `model`), dois testes de
  integração em `_persist_success` confirmando que um candidato
  rejeitado nunca cria `Product`/`Offer`/`PriceObservation` nem chama
  `classify_offer_relevance`, e que a Amazon sem `model` nunca escolhe
  "a mais barata".
- **Validação manual real** (perfil `ADMIN`, nunca `USER`) contra o
  `AdminDevAIProviderManager`: `"quero uma 4070 ti"` →
  `search_query: "Placa de Vídeo NVIDIA RTX 4070 Ti"`,
  `model: "RTX 4070 Ti"`; `"procura um 9800x3d"` →
  `search_query: "Processador AMD Ryzen 7 9800X3D"` (família correta,
  `Ryzen 7`, não `Ryzen 9`), `model: "9800X3D"`; `"quero um 9950x3d ate
  3500"` (re-teste) → resultado inalterado; regressão confirmada em
  `"quero um mouse bom e barato"` (`model: None`, sem invenção) e
  `"procura um mouse logitek barato"` (correção de digitação da
  TASK-074 preservada).

## Próximo passo

Implementação concluída e validada. Commit local feito. Aguardando
autorização explícita do usuário para publicar (tag/push) e implantar
em produção — nenhum push nem deploy realizado ainda.
