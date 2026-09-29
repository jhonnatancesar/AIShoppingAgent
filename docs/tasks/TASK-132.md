# TASK-132 — Anúncio que muda de produto (troca automática de cadastro) e catálogo de nomenclaturas

Status: **Concluída localmente (2026-09-28), commit local, sem push/tag/deploy.**
Duas partes, ambas implementadas. Duas migrations novas (`20260928_0001` e
`20260928_0002`), sem flag nova (nascem ativas, sob a flag existente
`product_identity_learning_enabled`).

## Parte A — O anúncio passou a descrever outro produto

**Origem:** Offer `196b2fa2` (Amazon `B0D8WH9NG3`, GIGABYTE B550 AORUS Elite AX
V3): o mesmo link passou a abrir outra placa. Antes, a Offer ficava presa ao
Product antigo e o histórico do produto novo era gravado no antigo.

**Decisões do usuário (2026-09-28):** separar o histórico (Offer arquivada);
confirmar em 2 coletas seguidas; IA só quando o título muda.

**Como funciona (`app/offers/listing_change.py`):**
1. A cada coleta de uma Offer existente, `_resolve_offer` chama
   `track_listing_identity` (só banco, dentro de savepoint — nunca derruba a
   coleta). `lookup_title_identity` responde sem IA e sem gravar: extrator
   determinístico → catálogo → cache por título → reuso por palavras.
2. Identidade igual à do Product: a vigilância (`offer_identity_watch`) some.
   Diferente: acumula avistamentos do MESMO título; na 2ª coleta seguida aplica.
   Título com outro texto reinicia a contagem (oscilação A→B→A nunca troca).
3. Título sem identidade conhecida: marca `needs_ai`. A varredura
   `app/products/listing_title_sweep.py` (início do `run_batch`, orçamento
   `listing_title_check_budget`, default 3, no máximo 3 tentativas por título)
   resolve pelo caminho normal de identidade. Título estável nunca gasta IA.
4. `apply_listing_change`: cria uma Offer arquivada (Product antigo,
   `superseded_by_id` = a Offer viva, `external_id`/URL com sufixo
   `~arquivada-…`/`#arquivada-…`), move para ela as observações anteriores ao
   primeiro avistamento (e as linhas de `shared_collection_offers` e
   `offer_coupon_price_days` dessas observações), apaga a relevância de missão
   da Offer viva (reclassificada na próxima coleta) e passa a Offer viva para o
   Product novo. Se a coleta do título novo foi redundante e não gravou
   observação própria, a mais recente do período antigo fica com a Offer viva
   (representa o estado atual).
5. Offer com `PurchaseConfirmation` (evidência imutável) nunca é trocada: log
   `listing_change_skipped_purchase_confirmation`.

## Parte B — Catálogo de nomenclaturas

**Tabelas:** `product_identity_catalog_entries` (identidade: categoria, marca,
família, modelo, variante, atributos, atributos obrigatórios, status
`active|rejected`, fonte `seed|learned|manual`) e
`product_identity_catalog_codes` (nome ou part number normalizado, único por
tipo e valor, inclusive contra entradas recusadas).

**Consulta (`identity_catalog.py`):** roda ANTES do cache por título, do reuso e
da IA (`_prepare_resolution`). Part number (compactado, ≥6 caracteres com letra e
dígito) vale mais que nome; nome casa por token exato, com a guarda de palavras
de edição compartilhada (`identity_edition.py`: "Pro Max" nunca resolve para
"Pro"; "Pro+" ≠ "Pro"); empate ou ambiguidade = não resolve. Entrada com
atributo obrigatório (celular: capacidade) monta a identidade pelo caminho do
extrator (`resolve_catalog_family`), sem mudar as chaves já gravadas.

**Aprendizado:** toda identidade aprovada com part number vira (ou reforça) uma
entrada `learned`; código já cadastrado, inclusive recusado, nunca é reaprendido.

**Pré-lista:** `identity_catalog_seed.py` (iPhone 16e, Galaxy A/M/Z, Redmi Note
13/14 base/Pro/Pro+, Redmi, Poco, Moto G, Intel Core Ultra, Radeon RX, Intel
Arc). Scripts: `scripts/seed_identity_catalog.py` (`--dry-run`/`--apply`, também
aprende dos candidatos aprovados e relata conflito de part number) e
`scripts/review_identity_catalog.py` (`--list [--all]`, `--reject ID`,
`--activate ID`).

## Testes

Unitários: `tests/test_product_identity_catalog.py`, `tests/test_listing_change.py`,
`tests/test_listing_title_sweep.py`. Integração (Postgres real):
`tests/integration/test_identity_catalog.py`,
`tests/integration/test_listing_product_change.py` (2 coletas, histórico
preservado, oscilação, título desconhecido, teto de tentativas de IA, relevância
reiniciada). Suíte de integração completa: 406 passando (inclui o ciclo
upgrade→downgrade→upgrade e `alembic check` das duas migrations). Unitária:
2836 passando, cobertura 90%.

## Deploy (quando autorizado)

Migrations `20260928_0001` e `20260928_0002`; depois, `python -m
scripts.seed_identity_catalog --dry-run` e `--apply`. Conferir a Offer
`196b2fa2` após o primeiro ciclo de coleta.
