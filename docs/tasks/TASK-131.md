# TASK-131 — Identidade: iPhone 1/2 TB, palavras de edição (MAX/PZ/Pro Max) e RAM lida como CPU

Status: **Concluída em DEV (2026-09-28), commit local, sem push e sem tag.**
Vai na `v1.4.0` (tag só quando as tasks pendentes ficarem prontas, decisão do
usuário). Origem: relatório do backfill da TASK-123 na PROD com a `v1.3.29` e
correção manual do mesmo dia (`DEC-139`).

## O que a PROD mostrou

1. **iPhone 17 Pro Max de 1 TB fundido nos Pro de 1 TB** (5+ títulos gravados
   com `model=17-pro` e fundidos em "Apple iPhone 17 Pro (1 TB)"): o histórico do
   Pro ficou contaminado pelo Pro Max.
2. **Placas MSI "MAX" / "MAX PZ" fundidas nas versões sem MAX.**
3. **Memória RAM (Kingston FURY Beast 6000, Corsair Vengeance 5200) gravada como
   `cpu/amd/ryzen`**, com as versões RGB e sem RGB da Kingston fundidas.

Tudo isso já foi corrigido À MÃO no banco da PROD (Offers movidas, cache de
títulos alinhado, órfãos e kit Husky apagados, relatório no servidor). Esta
TASK corrige o CÓDIGO, para o erro não voltar quando a coleta ligar.

## Causas e correções (feitas)

- **A. Capacidade de 1 dígito nunca era lida.** `_STORAGE` em
  `app/products/identity.py` era `(\d{2,4})\s*(GB|TB)`: "1 TB"/"2TB" não casavam,
  apesar de o docstring dizer que 1/2 TB são convertidos. Todo iPhone/Galaxy de
  1 ou 2 TB saía do extrator determinístico, ia para a IA e recebia identidade
  no estilo da IA (`model=17-pro`, sem capacidade). Agora `\d{1,4}`; "8GB" (RAM)
  segue abaixo do piso de 64 GB.
- **B. Reuso por palavras do título ignorava palavras de edição.**
  `_find_reusable_candidate_by_tokens` e `_find_same_model_candidates`
  (`identity_learning.py`) aceitavam o primeiro candidato aprovado cujos tokens
  de marca/família/modelo aparecem no título. Nova guarda
  `_has_unknown_edition_word`: se o título traz uma palavra da lista fechada
  (MAX, PLUS, ULTRA, PRO, LITE, FE, PZ, TI, XT, XTX) que o candidato não conhece
  (marca/família/modelo/variante/atributos), o candidato é recusado e o título
  segue para a IA. Fora da lista, de propósito: AIR ("Air Cooler"), MINI
  ("Mini-ITX"), SUPER ("Super Retina"), SE. Custo assumido: algumas chamadas de
  IA a mais, em vez de fundir no produto errado.
- **E. Extrator de CPU lia memória RAM como Ryzen/Core.** "AMD EXPO"/"Intel XMP"
  mais "6000MT/s"/"5200MHz" bastavam. Nova guarda `_starts_as_memory_module`
  em `identity.py`: título que traz memória/memory/RAM/DIMM/SODIMM nos 6 primeiros
  tokens e nenhum "processador/CPU" ali não é CPU. "Memória DDR4" no meio da
  especificação de uma CPU real não bloqueia.

## Testes

- `tests/test_product_identity_engine.py`: capacidade 1/2 TB, iPhone Pro × Pro
  Max, RAM nunca vira CPU, CPUs reais continuam resolvendo.
- `tests/test_product_identity_learning_matcher.py`: guarda de edição (MAX, PZ,
  Pro Max, lista fechada, pontuação colada).
- `tests/integration/test_identity_edition_guard.py`: título com MAX vai para a
  IA e cria identidade própria; título sem MAX reaproveita o cadastro sem IA.

## Verificado e NÃO alterado (registrado)

- **C. Chave do extrator ≠ chave do construtor.** O extrator grava o atributo
  como `storage_gb`; `build_resolved_variant_from_fields` grava `storage-gb`.
  Mesmo produto, `identity_key` diferente. Não mexer nas chaves (mudaria as já
  gravadas); correção de dado feita com o próprio extrator. Entra no desenho do
  catálogo da TASK-132.
- **D.** O extrator de iPhone não reconhece "iPhone 16e" (`\b` depois do
  dígito). Os 3 anúncios só aparecem numa missão cancelada; fora de escopo.

## Sem migration, sem flag nova

Nenhuma mudança em `config.py`: as flags do GG seguem `default=True`.

## Fora desta TASK

- **OmniRoute (repositório `cesar-core`, instância separada em PROD):** o último
  passo das cascatas, `oc/mimo-v2.5-free`, responde 403. O catálogo nativo tem 8
  modelos gratuitos sem login; proposta: trocar o passo único por
  `oc/deepseek-v4-flash-free`, `oc/nemotron-3-ultra-free`, `oc/hy3-free`,
  `oc/north-mini-code-free` e `oc/mimo-v2.5-free`. Em standby por decisão do
  usuário; muda o roteamento de IA em PROD, só com aval.
- Anúncio que muda de produto e catálogo de nomenclaturas: `TASK-132`.
