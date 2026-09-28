# TASK-130 — Kit/combo nunca vira oferta, alias por prefixo e leitura de página quando a marca não bate com nenhum alias

Status: **Fechada (2026-09-28).** Itens 1 e 2 implementados, testados e
publicados. Item 3 **fica registrado para o futuro, não implementado**
por decisão do usuário — ver seção própria para o critério exato, caso
volte a valer a pena reabrir. Achada durante a análise do dry-run da
`v1.3.28` (TASK-129) na PROD, aprofundada em conversa com o usuário —
ver "Origem" em cada item.

Nenhuma flag nova, nenhuma flag alterada (`backend/app/core/config.py`
sem diff nesta TASK) — o item 1 nem passa por flag, sempre ativo; o
item 2 usa o mesmo `product_identity_learning_enabled` que já sobe
`true`. Confirmado antes do commit.

## Item 1 — Anúncio de kit/combo nunca deve virar `Offer` de um item avulso

**Status: feito e testado (2026-09-28).** `_RELEVANCE_SYSTEM_PROMPT`
em `app/collection/relevance.py` ganhou a regra kit/combo/bundle,
inclusive lendo o próprio `search_query` da missão pra abrir exceção
quando a busca pedir um kit de verdade. Teste:
`tests/test_offer_relevance.py::test_relevance_prompt_rejects_kit_combo_bundling_the_requested_item`
(a classificação em si é 100% IA — o teste verificado é o texto do
prompt; não há como testar o julgamento do modelo de forma
determinística). **Só vale para anúncio coletado a partir de agora** —
não retroage sobre `Offer`s já criadas antes desta versão (mesmo
padrão "só daqui pra frente" da TASK-125). Limpar combos já coletados
antes desta correção, se existir algum, fica de fora desta TASK.

**Origem:** correção do usuário (2026-09-27) sobre o achado do dry-run
"Processador AMD Ryzen 7 7800X3D + Placa Mãe ASUS… classificado como a
CPU sozinha". O usuário apontou a causa certa: **isso é problema de
relevância da busca, não de identidade** — "se eu pesquisei processador
blablabla e o título é kit placa-mãe e processador ou kit processador e
placa-mãe ou derivados, não era nem pro GG estar aceitando essa
pesquisa".

**Onde:** `classify_offer_relevance` /
`_RELEVANCE_SYSTEM_PROMPT` em `app/collection/relevance.py`. Essa
classificação já decide, antes de um anúncio virar `Offer` rastreada, se
ele corresponde ao pedido da missão (`match`/`possible_match`/
`no_match`). O prompt manda recusar produto diferente, acessório, peça
avulsa, categoria diferente, modelo diferente — mas **não menciona
kit/combo/bundle**. Um anúncio "Kit Processador 7800X3D + Placa-mãe
ASUS" cita a marca e o modelo certos da CPU pedida, então pode virar
`match` mesmo sendo dois produtos vendidos juntos, por um preço que não
é o da CPU sozinha.

**Correção proposta:** regra explícita no `_RELEVANCE_SYSTEM_PROMPT`:
anúncio que embala o item pedido junto com outro produto diferente
(kit/combo/bundle/"leve junto") é `no_match`, mesmo citando marca e
modelo certos — a não ser que a própria busca da missão peça um
kit/combo. Corrige na raiz: o anúncio nunca chega a virar `Offer`, nunca
chega no backfill de identidade, o problema de "vira CPU sozinha" deixa
de existir.

## Item 2 — Alias por prefixo (marca escrita como a linha inteira)

**Status: feito e testado (2026-09-28).** `IdentityVocabulary._alias`
(`identity_vocabulary.py`) agora tenta igualdade exata primeiro e, se
não bater, casa por PREFIXO DE TOKEN inteiro (nunca substring solta —
"furyx"/"furioso" não batem com "fury"; em empate, o alias mais
específico vence). Vale para `canonical_value` (canonicalização) e por
consequência para `canonicalize_fields`, usado tanto no caminho ao vivo
(`identity_learning.py`) quanto no script de dedupe/backfill
(`reprocess_unresolved_product_identity.py`) — os dois compartilham o
mesmo `IdentityVocabulary`, sem precisar de nenhuma mudança no script
em si. Provado com um teste que roda o SCRIPT de dedupe de ponta a
ponta:
`tests/integration/test_reprocess_unresolved_product_identity.py::test_apply_uses_alias_by_prefix_through_the_dedupe_script`.
Testes unitários em `tests/test_product_identity_vocabulary.py`.

**Origem:** achado do dry-run — "Memória Fury Beast" extraído com
`brand=fury-beast` (a linha inteira, não só "Fury"). A tabela de
aliases (TASK-129) tem `fury -> kingston`, mas a comparação hoje é só
por igualdade exata do valor inteiro (`identity_vocabulary.py`,
`IdentityVocabulary._alias`/`alias_source_in`) — `fury-beast` nunca bate
com a chave `fury`.

**Correção proposta:** a comparação também aceita quando o valor
observado **começa** com a grafia do alias (`fury-beast` começa com
`fury`), mantendo o alias por categoria como hoje. Vale tanto para a
canonicalização (`canonical_value`) quanto para o grounding
(`alias_source_in`). Puro lookup em banco, sem chamada de IA extra, sem
abrir página.

## Item 3 — Leitura de página quando a marca aprovada não bate com nenhum alias conhecido

**Status: reavaliado (2026-09-28), NÃO implementado — aguardando
decisão do usuário.** Com os itens 1 e 2 prontos, o caso que sobra pra
esse item é bem mais estreito: só ajuda quando (a) a marca é uma linha
nunca vista antes, SEM alias nenhum cadastrado, E (b) ainda não existe
outro anúncio aprovado com o mesmo part number pra aprender por prova
(TASK-129). É o "primeiro anúncio de uma marca nova mal escrita" — caso
de borda, não o grosso do problema, que os itens 1 e 2 já cobrem. Abrir
página de produto é navegação real (custo e risco de bloqueio/CAPTCHA),
então decidi não implementar sem confirmar que ainda vale o custo pra
esse caso raro que sobrou. Segue como estava documentado antes:

**Origem:** o usuário perguntou por que a página do produto (que já tem
"Marca: Kingston" separado de "Modelo: Fury Beast" em dados estruturados
JSON-LD, confirmado em `product_page.py:99-108`) não é usada pra
resolver esse caso. Resposta técnica encontrada em conversa: a leitura
de página da TASK-128 só dispara quando a IA não consegue nem
categorizar o título (`awaiting_page`/`unrecognized`). "Fury Beast"
categorizou certo e foi `approved` — nunca entra nesse critério, mesmo
tendo dado errado na marca.

**Correção proposta (mais delicada, pensar no critério antes de
implementar):** ampliar o gatilho da leitura de página para também
cobrir "aprovado, mas a marca não bate com nenhuma família/alias já
conhecido" — sinal de que pode ser uma linha sendo confundida com
fabricante. Precisa de um critério que não dispare demais (custo de
abrir página é real, é navegação de verdade) — por exemplo, só quando
já existe candidato aprovado de outra marca com o mesmo part number
(prova mais forte que "parece estranho"). Repensar o critério exato
antes de implementar; pode acabar não sendo necessário se o item 2
(alias por prefixo) e o aprendizado por part number (TASK-129) já
cobrirem a maioria dos casos na prática.

## Encerramento (2026-09-28)

Usuário: "fecha mais deixa salvo essas info caso de problema
futuramente" — TASK fechada com 1 e 2 prontos; item 3 fica só
registrado (seção acima), não implementado, sem prazo. Se um caso real
do tipo "marca nova mal escrita, sem alias, sem outro anúncio com o
mesmo part number" aparecer de novo (ex.: em um dry-run ou relatório de
`--apply` futuro), este documento já tem o critério pronto para decidir
se vale reabrir.

## Fora de escopo desta TASK

- Normalização de grafia tipo "WiFi"/"Wi-Fi"/"WIFI" — **não é bug**:
  confirmado em conversa que `values_match_ignoring_punctuation` (usada
  no casamento de variante e de part number na zona cinzenta do
  árbitro) já ignora pontuação/maiúscula, então esses casos já se
  fundem sozinhos quando os dois títulos existem no banco na mesma
  sessão (`--apply` real). O dry-run mostrou como "separado" só porque
  desfaz cada lote antes do próximo — limitação do modo de teste, não
  do código.
