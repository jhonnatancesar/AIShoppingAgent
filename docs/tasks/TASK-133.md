# TASK-133 — Catálogo de nomenclaturas como fonte principal de identidade (IA só como último recurso)

Status: **Em andamento (2026-09-29): etapa 1 implementada e commitada local; demais etapas não iniciadas.** Pedido do usuário
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

## Progresso

**Etapa 1 — registro das falhas de IA: implementada (2026-09-29), commit local.**
Migration `20260929_0001` (status `ai_failed`; colunas `ai_attempts`,
`ai_error_kind`, `next_retry_at`). O extrator deixou de devolver `None` e passou
a devolver `AIExtractionFailure(kind)` (`quota`, `provider_unavailable`,
`timeout`, `provider_error`, `invalid_response`, `request_rejected`).
`app/products/identity_ai_failure.py`: `record_ai_failure`, espera crescente
(infraestrutura 15 min até 6 h; conteúdo 15 min, 1 h, 6 h, 24 h), teto de 5
tentativas só para falha de conteúdo (então `awaiting_page` se há produto de
origem, senão `unrecognized`), disjuntor **compartilhado** (tabela `identity_ai_breaker`, uma linha, na mesma
migration; vale para worker, API e scripts e sobrevive a restart; o cache em
memória é só local; 5 min ou até o reset da cota, no máximo 1 h). `_prepare_resolution` respeita `next_retry_at`; a decisão
real substitui a linha `ai_failed`. A varredura de página devolve a tentativa
quando a IA está fora, para queda de IA nunca virar `unrecognized`. Caminho em
lote (backfill) também registra e respeita o disjuntor. Testes unitários e de
integração novos; integração completa e unitária passando.

**Três correções do disjuntor: implementadas (2026-10-01), commit local.** (1) o aviso
é gravado numa transação própria e curta, com commit imediato, e sobrevive a
rollback do chamador e ao `--dry-run`; (2) a leitura roda num savepoint, então erro de
leitura (tabela ausente, banco instável) nunca aborta a transação do chamador; (3) no
máximo 2 chamadas de IA de identidade ao mesmo tempo por processo (um portão por loop
de eventos), e quem espera confere o disjuntor local logo antes de chamar — com a IA
fora do ar só as 2 primeiras gastam chamada. No caminho em lote (backfill) o portão só
limita a concorrência e a nova tentativa depois da pausa (TASK-129) continua valendo.
Integração 418 e unitária 2860 (90%) passando.

## Decisões do usuário (2026-09-28/29)

- Regra: o sistema usa o catálogo; se ele não tiver, tenta a busca; só então IA.
- **Busca de identidade fica no César Core.** O GG apenas solicita a busca e
  envia o pedido ao Core; a lista de sites, a leitura e o consenso vivem no
  Core. Sites: os já sugeridos (**TechPowerUp, PassMark, Adrenaline, Hardware
  Barato**, e outros de confiança que o usuário indicar) **mais os sites
  oficiais das próprias marcas**; a lista por categoria é mantida no Core,
  aprovada pelo usuário. Regra do projeto: o GG nunca fala direto com site nem
  provedor.
- Nenhum texto externo é entregue a IA nesse caminho: o Core extrai só campos
  fechados (categoria, marca, família, modelo, variante, part number,
  atributos) e devolve estruturado. Aceita só com consenso (2+ fontes da lista,
  ou 1 fonte estruturada + catálogo); sem consenso, devolve `ambiguous`.
- Nenhum título passa pela IA sem deixar um candidato: falha de IA vira
  candidato `ai_failed` (com motivo, tentativas e `next_retry_at`).

## Escopo proposto (ordem de execução)

1. **Registrar falhas de IA:** status `ai_failed` no candidato, tipo do erro,
   contagem, `next_retry_at` com espera crescente e teto de tentativas; o
   extrator devolve o motivo em vez de `None`; `_prepare_resolution` respeita o
   prazo (nada de chamada de IA a cada coleta); passado o teto, o título segue
   para leitura de página (`awaiting_page`) e por fim `unrecognized`. Disjuntor
   global para erro de cota/provedor. Migration (status e colunas).
2. **Aprender também por nome:** toda identidade aprovada (com ou sem part
   number) grava marca + família + modelo (+ variante e atributos obrigatórios)
   como entrada `learned`, com a guarda de palavras de edição e sem sobrescrever
   entrada recusada.
3. **Busca de identidade via Core (lado GG):** novo passo entre o catálogo e a
   IA. O GG envia título (e part number, se houver); o Core responde
   `resolved | ambiguous | not_found | unavailable` com campos estruturados e
   as fontes. O GG **revalida** o resultado com o próprio grounding
   determinístico contra o título antes de aceitar (nunca confia às cegas) e
   grava candidato/catálogo. `unavailable` ou erro do Core não derruba a coleta.
4. **Leitor de página em cadeia** (Core Fetch e, se falhar, worker) para o que a
   busca não resolver; teste real por loja antes de confiar.
5. **Ampliar a cobertura** da pré-lista (placas-mãe primeiro; depois RAM; depois
   categorias soltas: soundbar, cadeira gamer, antena, capa) com entradas só de
   categoria para vínculo parcial sem IA; revisão do usuário antes de aplicar.
6. **Unificar com as grafias de marca** (`product_identity_aliases`), sem quebrar
   as 47 grafias ativas.
7. **Deduplicação a partir do catálogo:** modo do script de reprocessamento que
   funde os que o catálogo mostra serem a mesma identidade, sem IA. Só com
   backup, dry-run e autorização.
8. **Qualidade e revisão:** conflitos de part number, nomes que se chocam,
   entradas suspeitas; `scripts/review_identity_catalog.py`.
9. **Medição** por categoria, por tipo de erro e por etapa que resolveu (título,
   catálogo, busca, página, IA).

Ordem final de resolução: extrator determinístico → catálogo → cache por título →
reuso por palavras → **busca no Core** → página (Core, depois worker) → IA.

## Dependência no César Core (repositório `cesar-core`, fora deste)

Capacidade nova "identidade de produto por busca": lista de domínios permitidos
(TechPowerUp, PassMark, Adrenaline, Hardware Barato e os oficiais das marcas)
por categoria/marca, extração determinística de campos fechados, consenso entre
fontes, sem entregar conteúdo a IA, tempo e volume limitados por pedido. O
contrato exato (campos e códigos de status) é definido junto com o usuário antes
de qualquer código; o GG só implementa o cliente e a revalidação.

## Levantamento de PROD (2026-09-29, consultas somente leitura)

~270 títulos foram à IA: 174 aprovados, ~90 parciais, 7 em revisão. Placas-mãe
98 (86 aprovadas, só 6 com part number); RAM 118 (63 aprovadas, 53 parciais, 18
com part number); categorias soltas ~33 (todas parciais); celulares 17; CPU 3.
Conclusão: aprender por nome e cobrir placas-mãe e RAM rende mais.

## Levantamento de partida (consultas somente leitura em PROD)

1. Candidatos que foram à IA, por categoria e status (`product_identity_candidates`
   com `ai_provider` preenchido), com quantos trazem part number.
2. Famílias aprovadas que mais se repetem (marca, família, modelo com 3+ títulos).
3. Cobertura atual do catálogo por fonte e status (só existe depois da `v1.4.0`).

## Fora de escopo

Mudar as chaves de identidade já gravadas (divergência `storage_gb` ×
`storage-gb`, TASK-131 causa C); trocar os passos gratuitos das cascatas do
OmniRoute; deploy em PROD sem autorização.
