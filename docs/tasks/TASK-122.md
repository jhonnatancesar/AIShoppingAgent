# TASK-122 — Terabyte/Amazon/Kabum/Mercado Livre tratam "busca sem oferta" como bloqueio (`provider_blocked`)

Status: **Implementada e testada (2026-09-20).** Achado real em PROD
(auditoria pós-deploy da `v1.3.15`/`v1.3.16`): `CollectionRun` da
Terabyte marcado `failed`/`provider_blocked` para a missão "Apple
iPhone 17 Pro" (loja que não vende esse produto), sem bloqueio
confirmado -- `mission_sources.consecutive_blocks=0`, nenhum backoff
aplicado, e a Terabyte teve sucesso na tentativa imediatamente anterior
e em todas as seguintes. Painel de controle mostrou isso como "1
falha", sem distinguir de um bloqueio real.

**Investigação ao vivo (Edge/CDP real, dedicado, porta 9333, mesmo
transporte da produção) mudou o desenho original desta TASK** -- ver
"Achados da investigação ao vivo" abaixo antes de ler "Objetivo".

## Problema

Das 6 lojas ativas, só a Pichau distinguia "página confirmou que não
há resultado para esta busca" de "erro ambíguo/possível bloqueio". As
outras 4 que usam o atalho genérico `EdgeCdpTransport.run()` --
Terabyte, Amazon, Kabum e Mercado Livre -- tratavam **toda** busca sem
oferta extraída como `ProviderBlockedError`, indistinguível no
`failure_code` de um bloqueio real do anti-bot.

- `backend/app/collection/providers/edge_cdp_transport.py:124-141`
  (`EdgeCdpTransport.run()`): só chama `extract(page)` depois que o
  `readiness_selector` respondeu (`state="attached"`) -- se nunca
  responder, levanta `EdgeCdpTransportError` (`_failure_code`
  `"provider_unavailable"`, código DIFERENTE, correto, não mexido por
  esta TASK).
- `backend/app/collection/providers/stores.py`:
  `TerabyteProvider._extract_via_cdp_page`,
  `AmazonProvider._extract_via_cdp_page`,
  `KabumProvider._extract_via_cdp_page`,
  `MercadoLivreProvider._extract_via_cdp_page` -- as quatro eram
  idênticas: `if not offers: raise ProviderBlockedError(self.source_code,
  None)`, incondicional, uma vez que o `extract()` real já rodou (ou
  seja, a página JÁ tinha carregado de verdade -- não é o mesmo caso do
  timeout acima).
- `backend/app/collection/orchestration.py:3744-3752` (`_failure_code`):
  qualquer `ProviderBlockedError` vira `failure_code="provider_blocked"`,
  **sem checar `error.status`**.
- `backend/app/collection/orchestration.py:3229-3239`
  (`_is_confirmed_external_block`, `_CONFIRMED_BLOCK_STATUSES =
  frozenset({403, 429})`): só 403/429 REAIS acionam backoff persistente
  -- **achado-chave desta investigação**: as quatro lojas SEMPRE
  chamavam `ProviderBlockedError(self.source_code, None)` (status
  `None`, nunca um código HTTP real) neste ponto específico -- ou seja,
  `_is_confirmed_external_block` NUNCA retornava `True` para essa
  chamada, mesmo antes da correção. A versão antiga não protegia contra
  nenhum bloqueio real comprovado aqui -- só marcava a run como `failed`
  sem necessidade nenhuma.

Magalu não é afetada -- usa parsing de JSON SSR embutido na página
(`_MagaluNextDataParser`), mecanismo totalmente diferente, sem esse
padrão de `ProviderBlockedError` condicionado a `not offers`.

## Achados da investigação ao vivo (Edge/CDP real, 2026-09-20)

Antes de implementar, testei as 4 lojas com um Edge dedicado real
(mesmo transporte `EdgeCdpTransport`/`backend/scripts/
validate_store_providers.py` que a produção usa) para confirmar o
comportamento real, não supor:

- **Amazon/Kabum**, busca genuinamente aleatória: texto limpo de "sem
  resultado" (`"Nenhum resultado"`/`"Lamentamos, nenhum produto
  encontrado com esse critério de pesquisa."`), 0 elementos no seletor
  real. Comportamento simples, confirma a hipótese original.
- **Terabyte**, busca real "Apple iPhone 17 Pro" (loja de hardware, não
  vende celular): a página NÃO mostra "sem resultado" -- mostra
  "Exibindo 3 de 3 produtos encontrados", mas são 3 controles de
  videogame 8BitDo, todos **Esgotado/Indisponível** (sem preço). Via o
  transporte REAL do projeto (não uma inspeção de DOM simplificada), a
  extração real (`extract()` -> `offers_from_rows`,
  `backend/app/collection/providers/base.py:794-814`) **já descarta**
  esses cards por falta de preço numérico válido -- reproduzido ao vivo
  o `ProviderBlockedError` exato do incidente original, na mesma linha.
- **Terabyte, busca genuinamente aleatória**: a página cai numa seção
  "CONFIRA ALGUMAS SUGESTÕES!" com produtos **disponíveis, com preço
  real** (ex.: "Controle Gamer 8BitDo Ultimate 2C" por R$ 199,99) --
  totalmente irrelevantes à busca, mas passam pelo filtro de preço e
  viram `RawCollectedOffer`s "válidas". **Isto não é o bug desta TASK**
  (a run não falha, tem sucesso com ofertas) -- é responsabilidade da
  camada de relevância já existente (`_deterministic_product_relevance`,
  `orchestration.py:1904-1928`) marcar como `NO_MATCH` quando o
  `identity_key` da oferta não bate com o produto pedido pela missão
  (`SPECIFIC_PRODUCT`). Confirmado que esse mecanismo já existe e já é
  a defesa correta para esse caso -- **registrado aqui como observação,
  não como escopo desta TASK** (ver "Fora de escopo").
- **Mercado Livre**: bateu num muro de login ao testar pelo navegador
  embutido do Claude (sessão/fingerprint daquele navegador
  especificamente), mas **não** pelo transporte real
  (`EdgeCdpTransport`) -- retornou 3 ofertas reais de outros iPhones
  (16e, 15, 16), não o 17 Pro. Resultado plausível/relevante (mesma
  marca, categoria certa, só não o modelo exato) -- também cai sob a
  responsabilidade da camada de relevância, não desta TASK.

**Conclusão prática:** a correção não precisa de um `empty_result_locator`
por loja (abordagem (a) do desenho original) nem de generalizar o
transporte (abordagem (b)) -- ambas ficaram desnecessárias. Uma vez que
`EdgeCdpTransport.run()` já garante que `extract()` só roda depois da
página carregar de verdade (readiness_selector respondeu), **zero
ofertas depois da extração real (já filtrada por preço/título/url
válidos) é sempre um resultado de sucesso vazio, nunca um bloqueio** --
bastou remover a checagem `if not offers: raise ProviderBlockedError(...)`
das quatro lojas, sem adicionar nenhuma lógica nova de detecção de
"vazio confirmado".

## Objetivo (implementado)

Remover `if not offers: raise ProviderBlockedError(self.source_code, None)`
de `TerabyteProvider`/`AmazonProvider`/`KabumProvider`/
`MercadoLivreProvider._extract_via_cdp_page`
(`backend/app/collection/providers/stores.py`) -- `_extract_via_cdp_page`
passa a sempre devolver `await self._resolve_unknown_availability(page, offers)`
(que já lida bem com tupla vazia), resultando num `CollectionResult`
de sucesso com `offers=()` quando a extração real não achar nada
aproveitável.

## Escopo

Só a remoção da checagem incondicional nas 4 lojas citadas (Terabyte,
Amazon, Kabum, Mercado Livre). Nenhuma mudança em
`EdgeCdpTransport.run()`/`PlaywrightStoreProvider` (não precisou).

## Fora de escopo

Pichau (já correta, não mexida) e Magalu (mecanismo diferente, não
afetada). `_is_confirmed_external_block`/backoff de bloqueio real
(403/429) -- já funciona corretamente, não mexido. Taxonomia de
`_failure_code` para outros tipos de erro -- não mexida. **A camada de
relevância** (`_deterministic_product_relevance`) que filtra ofertas
irrelevantes-mas-disponíveis (achado da Terabyte/sugestões e do Mercado
Livre acima) -- já existe, já cobre o caso para `SPECIFIC_PRODUCT`,
nenhuma mudança feita ou necessária nesta rodada; fica registrado como
observação para referência futura, não como pendência.

## Validação (concluída)

- `tests/test_store_providers.py`: 4 testes novos, um por loja
  (`test_{terabyte,amazon,kabum,mercado_livre}_zero_relevant_offers_after_real_page_load_is_empty_success_not_blocked`)
  -- provam que `extract()` retornando `()` depois de um `readiness_selector`
  bem-sucedido agora resulta em `CollectionResult.offers == ()`
  (sucesso), nunca mais `ProviderBlockedError`. Suíte completa do
  arquivo: **102 passed** (98 já existentes + 4 novos), zero regressão.
- `ruff check`/`format` limpos nos dois arquivos tocados.
- Suíte unitária completa (`tests/`, fora de `test_store_providers.py`)
  **não pôde ser revalidada de ponta a ponta nesta rodada**: rodar
  `tests/` inteiro (2600+ testes) nesta máquina esbarra numa fixture de
  sessão pré-existente e não relacionada (`tests/conftest.py`, Edge de
  sessão dedicado da suíte, porta 9333) que falha ao lançar o
  subprocess do Edge quando a suíte inteira roda de uma vez -- **mesmo
  arquivo/teste passa limpo quando rodado isolado ou em arquivos
  menores**, confirmando que não é regressão desta mudança (o código
  tocado nesta TASK não usa essa fixture nem lança processos). Registrado
  honestamente como limitação ambiental desta rodada, não escondido --
  mesma categoria de gremlin de ambiente Windows já documentada no
  projeto (ACL do diretório temp do pytest).
- **Validação real em PROD** (repetir a missão "Apple iPhone 17 Pro" na
  Terabyte e confirmar que não aparece mais como `failed` no painel)
  ainda pendente -- só acontece depois do deploy desta correção.
