# TASK-122 — Terabyte/Amazon/Kabum/Mercado Livre tratam "busca sem oferta" como bloqueio (`provider_blocked`)

Status: **Registrada, não iniciada.** Achado real em PROD em 2026-09-20
(auditoria pós-deploy da `v1.3.15`/`v1.3.16`): `CollectionRun` da
Terabyte marcado `failed`/`provider_blocked` para a missão "Apple iPhone
17 Pro" (loja que não vende esse produto), sem bloqueio confirmado --
`mission_sources.consecutive_blocks=0`, nenhum backoff aplicado, e a
Terabyte teve sucesso na tentativa imediatamente anterior e em todas as
seguintes. Painel de controle mostrou isso como "1 falha", sem
distinguir de um bloqueio real.

## Problema

Das 6 lojas ativas, só a Pichau distingue "página confirmou que não há
resultado para esta busca" de "erro ambíguo/possível bloqueio". As
outras 4 que usam o atalho genérico `EdgeCdpTransport.run()` --
Terabyte, Amazon, Kabum e Mercado Livre -- tratam **toda** busca sem
oferta extraída como `ProviderBlockedError`, indistinguível no
`failure_code` de um bloqueio real do anti-bot.

- `backend/app/collection/providers/edge_cdp_transport.py:124-141`
  (`EdgeCdpTransport.run()`) -- docstring já admite a limitação:
  "Providers com distinção real entre 'vazio' e 'bloqueado' (ex.:
  Pichau) usam `open_page` diretamente."
- `backend/app/collection/providers/stores.py`:
  `TerabyteProvider._extract_via_cdp_page` (:323-327),
  `AmazonProvider._extract_via_cdp_page` (:456-460),
  `KabumProvider._extract_via_cdp_page` (:590-594),
  `MercadoLivreProvider._extract_via_cdp_page` (:702-706) -- as quatro
  são idênticas: `if not offers: raise ProviderBlockedError(self.source_code,
  None)`, incondicional, sem checar se a página confirmou "sem
  resultado" antes de assumir bloqueio.
- Compare com `PichauProvider._collect_once` (:212-234): checa
  `empty_result_locator` primeiro (`_wait_for_results_or_empty`) -- se a
  página confirmar vazio, retorna `CollectionResult` com `offers=()`
  como **sucesso**, nunca levanta exceção. Só levanta
  `ProviderBlockedError` se a página não confirmou vazio E `extract()`
  ainda assim não achou nada.
- `backend/app/collection/orchestration.py:3744-3752` (`_failure_code`):
  qualquer `ProviderBlockedError` vira `failure_code="provider_blocked"`,
  **sem checar `error.status`** -- por isso um `status=None` (busca
  vazia, não bloqueio) e um `status=403` (bloqueio real confirmado)
  aparecem com o mesmo `failure_code` no evento/painel de controle,
  indistinguíveis para quem olha de fora.
- `backend/app/collection/orchestration.py:3229-3239`
  (`_is_confirmed_external_block`) já faz essa distinção internamente
  (só 403/429 acionam backoff persistente, `_CONFIRMED_BLOCK_STATUSES =
  frozenset({403, 429})`) -- o gap é que o `failure_code`/estado exposto
  (evento, `CollectionRun.status`) não reflete essa mesma distinção; a
  run é marcada `failed` de qualquer jeito.

Magalu não é afetada -- usa parsing de JSON SSR embutido na página
(`_MagaluNextDataParser`), mecanismo totalmente diferente, sem esse
padrão de `ProviderBlockedError` condicionado a `not offers`.

## Objetivo

Dar a Terabyte, Amazon, Kabum e Mercado Livre o mesmo tratamento que a
Pichau já tem: distinguir "página confirmou que não há resultado para
esta busca" (sucesso, `CollectionResult` vazio) de "erro
ambíguo/possível bloqueio" (falha real). Duas abordagens possíveis,
decisão de qual cabe a quem for implementar:

**(a)** Dar a cada uma das 4 lojas seu próprio
`empty_result_locator`/checagem de "sem resultado" análoga à Pichau,
migrando de `EdgeCdpTransport.run()` para `open_page` direto (mesmo
padrão que a Pichau já usa). Mais preciso por loja (cada uma tem seu
próprio texto/seletor de "nenhum resultado encontrado"), mais código
repetido entre as 4.

**(b)** Generalizar o mecanismo: um `empty_result_locator` opcional em
`EdgeCdpTransport.run()`/`PlaywrightStoreProvider`, cada provider que
tiver um seletor de "sem resultado" real o declara; providers sem esse
seletor continuam com o comportamento atual (indeciso permanece
indeciso, não piora nada). Menos repetição, mas mexe no transporte
compartilhado por todas as lojas que o usam.

Qualquer uma das duas: **nunca inferir "vazio" pela ausência do
seletor de resultado** (isso já é achado ambíguo/timeout, tratado à
parte via `EdgeCdpTransportError`) -- só quando a loja **confirma
textualmente** que não há resultado para a busca.

## Escopo

Só a distinção vazio-confirmado vs. erro-ambíguo nas 4 lojas citadas
(Terabyte, Amazon, Kabum, Mercado Livre).

## Fora de escopo

Pichau (já correta, não mexe) e Magalu (mecanismo diferente, não
afetada). Qualquer mudança em `_is_confirmed_external_block`/backoff de
bloqueio real (403/429) -- já funciona corretamente, não é o achado
aqui. Qualquer mudança na taxonomia de `_failure_code` para outros tipos
de erro (`circuit_open`, `normalization_failed`, `provider_unavailable`)
-- fora do escopo deste achado.

## Critério de validação futuro

Teste unitário/controlado por loja: busca que a própria loja confirma
"sem resultado" (mockando o seletor de vazio confirmado) retorna
`CollectionResult` vazio, sucesso -- não mais `ProviderBlockedError`.
Busca ambígua (readiness selector nunca aparece, ou aparece mas sem
seletor de "vazio confirmado" e `extract()` não acha nada) continua
caindo em `ProviderBlockedError` como hoje -- não afrouxar a detecção
real de bloqueio, zero regressão nas 4 lojas. Validação real: repetir
uma missão para um produto que uma dessas lojas comprovadamente não
vende (ex.: "Apple iPhone 17 Pro" na Terabyte) e confirmar que a run
correspondente não aparece mais como `failed` no painel de controle.
