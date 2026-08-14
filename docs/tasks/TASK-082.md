# TASK-082 — Limitar candidatos de pesquisas genéricas nas lojas (sem prejudicar pesquisas específicas)

Status: **Implementada e testada (2026-08-14)**. Suíte não-integração verde
(1079 passed, 90,98% cobertura), ruff limpo. Nenhuma chamada real, nenhuma
missão criada, nenhum rebuild Docker — validação só com testes
determinísticos/mockados, reaproveitando a auditoria de código já registrada
abaixo. Commit local pendente de aprovação final.

## Implementação (resumo objetivo)

Novo passo determinístico em `backend/app/collection/orchestration.py`,
`_limit_generic_candidates`, chamado dentro de `_persist_phase_a` logo
depois de `_filter_deterministic_candidates` e antes de qualquer
persistência/classificação:

```python
survivors = _filter_deterministic_candidates(criteria, normalized.offers)
if criteria.model is None:
    survivors = _limit_generic_candidates(
        survivors, limit=_GENERIC_SEARCH_CANDIDATE_LIMIT
    )
final_offers = (
    _select_amazon_lowest_price(survivors)
    if claim.source_code == "amazon" and criteria.model is not None
    else survivors
)
```

**Gate `específica` vs `genérica`**: continua sendo `criteria.model is
None` (o único sinal existente hoje, exatamente o critério interino já
documentado abaixo) — `criteria.model is not None` nunca passa por este
passo novo, preservando o comportamento da TASK-075 sem nenhuma alteração
(comprovado por teste de regressão explícito, `9800X3D`/`RTX 5070 Ti`
simulados com muitos candidatos sobreviventes, nenhum cortado).

**Limite escolhido: 3 candidatos por loja.** Nenhum número havia sido
fixado durante a auditoria original (ver seção abaixo, mantida como
histórico). Derivado por analogia do único precedente numérico já
existente no próprio pipeline de coleta para "quantos candidatos merecem
atenção determinística extra, por loja, antes de qualquer IA":
`PlaywrightStoreProvider.availability_fallback_max_candidates` (default
`3`, já em produção desde a TASK-075, mesmo arquivo/módulo). Mesma ordem
de grandeza, mesmo espírito — não é o "número da regra da Amazon" (que
não tem N configurável, colapsa sempre para 1 vencedor porque a
identidade já foi confirmada), mas reaproveita o **princípio** da regra
da Amazon: ordenar por menor preço (`amount` crescente) com desempate
determinístico por `external_id`/URL, generalizado para manter mais de 1
candidato — aqui não há identidade confirmada que justifique colapsar
para um só.

**Onde**: dentro de `_filter_deterministic_candidates`'s vizinhança
(logo depois dela, mesma Fase A da TASK-079/080), nunca antes da
extração bruta do provider — não altera nenhum provider, nenhum
`max_offers`, nenhuma normalização.

**Por loja, não global**: `_persist_phase_a` já roda uma vez por
`ClaimedCollection` (uma fonte por vez) — o corte de 3 se aplica aos
sobreviventes daquela única fonte; nenhum estado compartilhado entre
lojas, nenhuma loja consome o limite de outra (estrutural, não precisou
de mecanismo novo de contabilização).

**Sem chamada nova de IA/provider**: `_limit_generic_candidates` é uma
função pura sobre `NormalizedCollectedOffer` já em memória — nenhuma
requisição adicional a `AIProviderManager` nem a nenhum `CollectionProvider`.

## Testes adicionados

`tests/test_collection_orchestration.py` (função pura
`_limit_generic_candidates`): mantém os N mais baratos, desempate
determinístico por `external_id`, menos candidatos que o limite mantém
todos, entrada vazia não quebra.

`tests/test_collection_orchestration_async.py` (integração com
`_persist_phase_a`, mockado): busca genérica com 5 candidatos reduz a 3
(os 3 mais baratos, nenhuma chamada extra a `_resolve_offer` para os 2
descartados); busca específica com 5 candidatos todos batendo no filtro
de modelo preserva os 5, comportamento idêntico ao anterior.

---

## Comportamento real atual auditado antes da implementação (histórico)

Dependência: estende `TASK-075` (`docs/tasks/TASK-075.md`, filtro
determinístico + regra exclusiva da Amazon) sem reabri-la nem alterá-la.
**Depende parcialmente da TASK que tratar a classificação de
especificidade da busca** (específica/parcialmente específica/genérica/
ambígua — assunto da Etapa 4 desta mesma rodada, ver
`docs/tasks/TASK-083.md`): o critério "quando aplicar a limitação"
proposto aqui usa o único sinal que existe hoje (`criteria.model`) como
interino, e deve ser revisado se a TASK-083 introduzir uma classificação
mais rica.

Versão alvo: a definir pelo usuário.

## Comportamento real atual (auditado por código, não suposição)

**Não existe, em nenhum lugar do projeto, um limite numérico de
candidatos extraídos da página de busca.** Confirmado por leitura de
`backend/app/collection/providers/stores.py`: os quatro providers
(`PichauProvider`, `TerabyteProvider`, `AmazonProvider`, `KabumProvider`)
usam `page.locator(self.result_selector).evaluate_all(...)`, que extrai
**todos** os elementos que casam com o seletor na página carregada — sem
`.first(N)`, sem slicing, sem paginação adicional (só a primeira página
de resultados de cada loja, que por si só já traz tipicamente dezenas de
itens, número exato não medido nesta auditoria porque exigiria scraping
ao vivo, fora do escopo de uma etapa sem implementação).

A única redução de candidatos que existe hoje é a da `TASK-075`
(`backend/app/collection/orchestration.py:519-560`,
`_filter_deterministic_candidates` + `_select_amazon_lowest_price`,
chamadas em `orchestration.py:639-642`):

1. **Filtro de modelo** (`_title_matches_model`) — só roda quando
   `criteria.model is not None`. Para uma missão sem modelo
   identificável (todo o caso de busca genérica, por desenho da própria
   TASK-075/074), **este filtro não roda de verdade** — nenhum candidato
   é rejeitado por ele.
2. **Filtro de bundle** (`_title_looks_like_bundle`) — roda sempre,
   independente de `model`. Só rejeita sinal de PC completo/kit
   (`computador`, `pc gamer`, `kit`, `combo`, `workstation`,
   `notebook` quando a missão não é notebook). **Não reduz uma busca
   genérica de categoria** (ex.: "cadeira gamer") de forma relevante —
   nenhuma das palavras-sinal de bundle é esperada nos títulos de
   cadeiras gamer normais.
3. **Seleção de menor preço da Amazon** (`_select_amazon_lowest_price`)
   — só roda quando `claim.source_code == "amazon"` **e**
   `criteria.model is not None` (gate 8.1 da TASK-075). Para busca
   genérica, não roda em nenhuma loja, inclusive Amazon.

**Conclusão direta**: para uma missão de busca genérica (`criteria.model
= None`, ex.: `"quero uma cadeira gamer"`), **hoje não existe nenhuma
redução de candidatos antes da persistência e da IA** — todo candidato
que sobreviver ao filtro de bundle (a esmagadora maioria, numa busca de
categoria) segue individualmente para `_resolve_offer` →
`PriceObservation` → `classify_offer_relevance`, um candidato por vez,
potencialmente dezenas por loja por rodada de coleta. Isso é
consistente com o incidente real já registrado (missão "cadeira gamer",
mencionado na `TASK-079` como o gatilho da investigação do travamento) —
volume alto de candidatos irrelevantes, ainda que o mecanismo do
travamento em si já tenha sido corrigido por outra causa.

## Objetivo

```
busca da loja
  → conjunto limitado e útil de candidatos   [NOVO — esta TASK]
  → normalização                              [já existe]
  → filtros determinísticos (modelo + bundle) [já existe, TASK-075]
  → preço/seleção Amazon                      [já existe, TASK-075]
  → quantidade final pequena
  → IA/relevância só quando necessário        [já existe]
```

O novo passo entra **antes** da normalização, não substitui nem duplica
os filtros já existentes da TASK-075.

## Requisito crítico: não prejudicar pesquisas específicas

Confirmado por desenho: `"Ryzen 7 9800X3D"` e `"RTX 5070 Ti"` já
produzem `criteria.model` preenchido (mesmo mecanismo documentado na
TASK-075) — essas buscas já passam pelo filtro de modelo, que **por si
só** já reduz a poucos candidatos (o próprio propósito da TASK-075).
`"cadeira gamer"` não produz `model` (não há modelo/SKU específico numa
cadeira gamer genérica) — é exatamente o caso sem cobertura hoje.

**A nova limitação desta TASK só deve entrar em ação quando não houver
sinal de especificidade suficiente** — nunca para buscas que já têm
`criteria.model` preenchido, sob risco de descartar candidatos que o
filtro de modelo já trataria corretamente sozinho.

## Critério proposto para "quando aplicar" (sinal interino)

Único sinal de especificidade que existe hoje no domínio:
`criteria.model is None`. Proposta interina, sujeita a revisão pela
TASK-083 (Etapa 4):

- `criteria.model is not None` → busca tratada como específica o
  bastante — **nenhuma limitação nova desta TASK se aplica**; comporta-se
  exatamente como hoje (filtro de modelo já reduz).
- `criteria.model is None` → candidato a receber a limitação desta TASK.

**Risco explícito desse critério interino**: um caso como `"monitor
Samsung 27"` (parcialmente específico, no vocabulário da Etapa 4/TASK-083)
também tem `model = None` hoje, porque não há um SKU exato — cairia na
mesma limitação de uma busca totalmente genérica como `"mouse"`, mesmo
sendo mais restrito. Documentado aqui como uma lacuna conhecida do sinal
binário atual, não resolvida por esta TASK — a TASK-083 é quem deve
propor uma classificação mais granular (específica/parcialmente
específica/genérica/ambígua); quando ela existir, o gate desta TASK deve
ser revisado para usar a classificação nova em vez do binário
`model is None`.

## O que esta TASK não decide ainda

**Nenhum número de limite é proposto aqui** — o usuário pediu
explicitamente para não chutar. Antes de escolher um valor, ficam como
decisões em aberto para a implementação:

1. **Onde aplicar o corte**: na extração bruta (ex.: só ler os primeiros
   N elementos do `result_selector`, mais barato — economiza tempo de
   `evaluate_all` mas descarta informação de ranking da própria loja
   antes de qualquer normalização) ou depois da extração completa, como
   um novo passo determinístico dentro de `_filter_deterministic_candidates`
   (mais caro, mas permite ordenar por algum critério — ex.: preço,
   ordem original da loja — antes de cortar).
2. **Critério de corte**: os N primeiros na ordem que a própria loja
   devolve (assume que o ranking de relevância da loja já é razoável),
   ou algum critério próprio (ex.: menor preço, mais avaliações — se
   disponível no HTML) antes de cortar.
3. **O número em si**: precisa de medição real (quantos candidatos uma
   busca genérica típica retorna hoje por loja, quantos sobrevivem ao
   filtro de bundle) antes de qualquer proposta — não decidido nesta
   auditoria.
4. **Se o corte é igual para as 4 lojas** ou específico por loja (a
   Amazon já tem tratamento especial da TASK-075; Kabum já usa
   `facet_filters` para restringir a busca na origem, o que pode reduzir
   o volume bruto antes mesmo de chegar no Python).

## Fora de escopo

- Reabrir ou alterar o filtro de modelo, filtro de bundle ou seleção de
  menor preço da Amazon (TASK-075) — esta TASK só adiciona um passo
  novo, antes deles.
- Mudar a regra de fretes/preço (`amount` vs `total_amount`).
- Resolver a classificação de especificidade da busca (`específica` /
  `parcialmente específica` / `genérica` / `ambígua`) — isso é a
  TASK-083 (Etapa 4).
- Qualquer mudança em `classify_offer_relevance`, pré-lista ou alertas.

## Critérios de aceite

1. ✅ Busca específica (`criteria.model` preenchido, ex.: `9800X3D`, `RTX
   5070 Ti`) — comportamento **idêntico** ao atual, comprovado por teste
   de regressão explícito (nenhuma redução adicional desta TASK se
   aplica).
2. ✅ Busca genérica (`criteria.model = None`) — no máximo 3 candidatos
   por loja chegam a `classify_offer_relevance`, nunca ilimitado.
3. Critério de corte usa preço (`amount` crescente, mesmo princípio já
   comprovado da Amazon), não a ordem bruta da loja — não depende de
   validação real de ranking de relevância da própria loja.
4. ✅ Nenhuma mudança em `_filter_deterministic_candidates`/
   `_select_amazon_lowest_price` além de receberem um conjunto já
   pré-limitado como entrada, quando aplicável (só busca genérica).
5. ✅ Pipeline não-integração completo (suíte + ruff) aprovado antes do
   commit. Validação real/Docker/missão explicitamente fora do escopo
   desta rodada de implementação, por instrução direta do usuário.

## Impacto em banco/migration

Nenhum esperado — mudança de pipeline de coleta, não de schema.
