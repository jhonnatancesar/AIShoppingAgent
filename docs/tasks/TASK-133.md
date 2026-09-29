# TASK-133 — Catálogo de nomenclaturas como fonte principal de identidade (IA só como último recurso)

Status: **Registrada (2026-09-28), planejada, NÃO iniciada.** Pedido do usuário
em conversa, na sequência da TASK-132 (Parte B, catálogo). Aguarda o resultado
das consultas de gasto de IA em PROD para ordenar as categorias.

## Objetivo

Deixar o catálogo "topíssimo" e fazer o sistema usá-lo como **fonte principal**
para resolver identidade de produto e duplicidade. Regra do usuário: **o
sistema usa o catálogo; se ele não tiver, aí sim usa IA.** A IA deixa de ser o
caminho normal e vira exceção, reduzindo o gasto de cota.

## Estado de partida (v1.4.0)

- O catálogo (`product_identity_catalog_entries`/`_codes`) é consultado dentro de
  `resolve_or_learn_product_variant`, antes do cache por título, do reuso por
  palavras e da IA. Os dois caminhos da flag `product_identity_learning_enabled`
  (coleta, Fase B/C) e o reprocessamento da TASK-123 passam por essa função
  (a confirmar linha a linha no início da TASK).
- Identidade exata resolvida (pelo catálogo ou pela IA) funde o produto ad-hoc no
  canônico via `apply_learned_identity`.
- O catálogo só aprende sozinho quando a IA devolve part number; a pré-lista é
  pequena (celulares, Redmi/Poco, Moto G, Intel Core Ultra, Radeon RX, Arc).

## Escopo proposto (a ordenar depois do levantamento de PROD)

1. **Aprender também por nome** (decidido pelo usuário): toda identidade
   aprovada, com ou sem part number, grava marca + família + modelo (+ variante e
   atributos obrigatórios) como entrada `learned`, com a guarda de palavras de
   edição (Pro/Max/Plus…) e sem nunca sobrescrever entrada recusada.
2. **Ampliar a cobertura** da pré-lista pelas famílias que mais repetem e mais
   gastam IA (notebooks, monitores, SSD, RAM, placas-mãe, fontes, GPUs, CPUs…),
   priorizadas pelas consultas de PROD; revisão do usuário antes de aplicar.
3. **Unificar com as grafias de marca** (`product_identity_aliases`): uma única
   fonte de verdade para "como se escreve isso", sem quebrar as 47 grafias ativas.
4. **Deduplicação a partir do catálogo:** modo do script de reprocessamento que
   varre produtos existentes e funde os que o catálogo mostra serem a mesma
   identidade, sem IA. Só com backup, dry-run e autorização do usuário.
5. **Qualidade e revisão:** relatório de conflitos de part number, nomes que se
   chocam e entradas suspeitas; revisão por `scripts/review_identity_catalog.py`.
   Um erro no catálogo se repete em todo título parecido, então recusa e
   reativação continuam sendo o freio.
6. **Medição:** contadores de quantos títulos o catálogo resolveu contra quantos
   foram à IA (por categoria), para provar a queda de gasto.
7. **Ordem de resolução mantida e explícita:** extrator determinístico →
   catálogo → cache por título → reuso por palavras → IA (só se nada resolver).

## Levantamento de partida (consultas somente leitura em PROD)

1. Candidatos que foram à IA, por categoria e status (`product_identity_candidates`
   com `ai_provider` preenchido), com quantos trazem part number.
2. Famílias aprovadas que mais se repetem (marca, família, modelo com 3+ títulos).
3. Cobertura atual do catálogo por fonte e status (só existe depois da `v1.4.0`).

## Fora de escopo

Mudar as chaves de identidade já gravadas (divergência `storage_gb` ×
`storage-gb`, TASK-131 causa C); trocar os passos gratuitos das cascatas do
OmniRoute; deploy em PROD sem autorização.
