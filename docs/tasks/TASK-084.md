# TASK-084 — Foto do produto, link curto e uma oferta por mensagem no Telegram

Status: **Auditada nesta rodada (2026-08-13, Etapa 5 do planejamento
pós-v1.0.6)** — fluxo atual mapeado ponta a ponta com evidência de
código; nenhuma implementação, nenhuma tecnologia de link curto
escolhida.

Dependência: nenhuma bloqueante das TASKs anteriores desta rodada.
Não reabre TASK-068 (pré-lista) nem TASK-075 (regra de preço) — só muda
**como** a mesma informação já decidida por elas é apresentada no
Telegram.

Versão alvo: a definir pelo usuário.

## Auditoria do fluxo completo (provider → Telegram)

Percorrido ponta a ponta por leitura de código, não suposição:

1. **Provider → `RawCollectedOffer`**
   (`backend/app/collection/contracts.py:38-59`): campos existentes —
   `source_code`, `url`, `title`, `external_id`, `seller_*`,
   `raw_price`, `raw_currency`, `raw_shipping`, `raw_availability`,
   `raw_fulfillment`, `evidence`. **Nenhum campo de imagem.**
2. **Os quatro Store Providers**
   (`backend/app/collection/providers/stores.py`) — auditados um a um,
   nenhum extrai imagem hoje:
   - `PichauProvider.extract`: `url`, `title`, `price`, `external_id`,
     `availability`, `evidence` — sem imagem.
   - `TerabyteProvider.extract`: mesmo conjunto de campos — sem imagem.
   - `AmazonProvider.extract`: inclui `seller`, `shipping`,
     `fulfillment` — ainda sem imagem.
   - `KabumProvider.extract`: inclui `seller`, `shipping` — sem imagem.
   - Nenhum dos quatro scripts JS (`evaluate_all`) referencia `img`,
     `src`, `srcset` ou qualquer seletor de imagem.
   - **Não verificado nesta auditoria** (exigiria inspecionar o HTML
     real de cada loja, fora do escopo de uma etapa sem execução): se
     cada card de busca das quatro lojas efetivamente expõe uma URL de
     imagem acessível no DOM. Presumível que sim (padrão universal de
     card de e-commerce), mas fica como item de investigação da
     implementação, não fato confirmado aqui.
3. **Normalização/persistência**: `Offer`
   (`backend/app/offers/models.py`) tem `url` como único campo de
   link — **sem coluna de imagem**. `Product`
   (`backend/app/products/models.py`) também não tem.
   `PriceObservation` (não lido campo a campo nesta auditoria, mas
   consistente com o padrão dos outros dois: observação de preço, não
   de mídia) — presumivelmente também sem imagem; a confirmar na
   implementação.
4. **Classificação/pré-lista**: `_evaluate_mission_prelist`,
   `_render_prelist_ready`/`_render_prelist_errata`
   (`backend/app/telegram/notifications.py`) — só leem `Offer.url`,
   `Product.display_name`/`.name`, `Store.name`, preço — nenhum dado de
   imagem para descartar (porque nunca existiu).
5. **Envio Telegram**: `backend/app/telegram/bot_api.py` implementa
   **só `sendMessage`** (texto puro, método `send_message`). **Não
   existe `sendPhoto`/`sendMediaGroup`/qualquer envio de mídia** na Bot
   API deste projeto hoje.

## Como as ofertas são agrupadas hoje (achado central)

`_render_prelist_ready` (`notifications.py:523-559`, TASK-068) monta
**uma única mensagem de texto** contendo até 2 blocos de oferta
concatenados com `"\n\n".join(blocks)` — exatamente o padrão que o
usuário quer eliminar ("Encontramos: 1. Amazon... 2. Kabum..." numa
mensagem só). Mesmo padrão em `_render_prelist_errata` (uma oferta só,
mas ainda como texto corrido dentro de uma mensagem maior com contexto).
`_prepare_notification`/o caminho de alerta de preço (linhas ~460-488)
também monta texto único com `offer.url` cru ao final.

**Confirmado: `offer.url` é enviado como está, sem encurtamento** — a
URL completa da Amazon/Pichau/Kabum/Terabyte (que pode ser longa,
principalmente Amazon com parâmetros de rastreamento) aparece
diretamente no texto da mensagem.

**Confirmado: nenhum mecanismo de link curto/redirect existe hoje** —
busca por `redirect`/`short_link`/`/r/` em todo `backend/app` não
encontrou nenhuma rota, tabela ou utilitário relacionado (o único
resultado, `_EDIT_MISSION_FREE_TEXT_REDIRECT` em `telegram/router.py`,
é uma constante de texto de mensagem, sem relação com URL).

## 5.A — Foto do produto

**Plano**: estender `RawCollectedOffer` com um campo opcional
`image_url: str | None` (mesmo padrão dos demais campos `raw_*`
opcionais); cada provider passa a extrair a URL da imagem do próprio
card que originou a oferta (seletor específico por loja, a levantar na
implementação — não presumido aqui). Persistir em `Offer` (ou, se a
imagem puder mudar por observação individual, decidir entre `Offer`
como identidade estável vs. algo por `PriceObservation` — decisão de
implementação, não desta auditoria; `Offer` parece o lugar mais natural
porque a imagem representa o anúncio, não uma variação de preço no
tempo).

**Regras obrigatórias** (do pedido do usuário, sem ambiguidade):

- Imagem sempre do card/página da própria loja que originou aquela
  oferta específica — nunca gerada por IA, nunca genérica de categoria,
  nunca de outro modelo.
- Se o provider expõe a imagem mas não extrai hoje: extrair (esta
  TASK). Se não existir/falhar a extração: a oferta é enviada mesmo
  assim, sem foto — **ausência de imagem nunca é motivo para descartar
  uma oferta válida**.
- Se o Telegram falhar ao buscar a URL da imagem no momento do envio
  (link quebrado, expirado, bloqueio da loja): fallback automático para
  texto sem foto na mesma tentativa — não pode derrubar o envio da
  oferta inteira (ver 5.C).

## 5.B — Uma oferta = uma mensagem

**Plano**: `_render_prelist_ready`/`_render_prelist_errata`/o caminho
de alerta de preço deixam de concatenar blocos numa string única — cada
oferta que hoje vira um "bloco" passa a virar **uma chamada de envio
independente**, numerada (`1️⃣`, `2️⃣`, ...), preservando a ordem de
ranking já decidida pelo pipeline existente (preço, TASK-075/068 —
**esta TASK não muda critério de ranking, só como cada item já
ranqueado é entregue**).

Efeito em `process_telegram_notifications`
(`backend/app/telegram/notifications.py:96-209`, TASK-080 relevante
aqui): hoje 1 evento → 1 `send_message`. Com N ofertas por evento de
pré-lista (hoje até 2, TASK-068), passa a ser 1 evento → N envios. Isso
**muda a relação evento→tentativa de entrega** registrada em
`EventConsumptionAttempt` — precisa de decisão explícita: todas as N
mensagens são atômicas (todas ou nenhuma conta como sucesso do evento)
ou cada uma é independente (uma falha não deve invalidar as outras já
enviadas)? Não decidido aqui — registrado como ponto que a
implementação precisa resolver, com atenção a não duplicar envio em
retry (se 2 de 3 mensagens já foram entregues e o consumo falhar por
causa da 3ª, um retry não pode reenviar as 2 primeiras).

## 5.C — Foto + texto na mesma unidade

**Plano**: adicionar `send_photo` a `bot_api.py`, usando o método
`sendPhoto` da Bot API (`photo` = URL pública da imagem, `caption` =
texto formatado da oferta) — mesma estrutura de `send_message`
(circuito, timeout, tratamento de erro), reaproveitando
`TelegramDeliveryError`/`TelegramBotAPIError` já existentes.

**Restrição técnica real da Bot API, não deste projeto** (a documentar
na TASK): `caption` de `sendPhoto` tem limite de 1024 caracteres,
menor que o limite de 4096 de `sendMessage` — o template proposto (5.E)
precisa caber nesse limite; se não couber, decisão de corte precisa ser
explícita (nunca truncar o link de compra no meio).

**Fallback obrigatório**: se `sendPhoto` falhar especificamente por
causa da imagem (Telegram não conseguiu buscar a URL — erro
identificável pela resposta da Bot API), reenviar o mesmo conteúdo via
`sendMessage` (sem foto) **na mesma tentativa de entrega**, não como um
novo evento — para não perder a oferta por causa só da imagem (mesma
regra do 5.A).

Confirmado que "separar" no pedido do usuário significa separar
**ofertas**, não os componentes internos de uma oferta — este item já
está coerente com 5.B: uma mensagem `sendPhoto` (foto+texto juntos) por
oferta.

## 5.D — Link curto

**Confirmado nesta auditoria**: nada disso existe hoje — nenhuma tabela,
rota ou utilitário de encurtamento/redirect no projeto.

**Opções levantadas** (nenhuma escolhida):

**Opção própria** (preferência já expressa pelo usuário, se simples e
segura): nova rota `GET` na `api` (ex.: `/r/{token}`, formato exato não
decidido) que faz `302 Found` para a URL original — exige, seguindo
exatamente os pontos que o usuário pediu para analisar:

- **Identificador**: token aleatório curto (ex.: `secrets.token_urlsafe`,
  mesmo padrão já usado para `telegram_webhook_secret`), não
  incremental/previsível (evita enumeração).
- **Persistência**: nova tabela (ex.: `offer_short_links`:
  `token` único, `offer_id` (FK para `Offer` existente — **nunca uma
  URL solta/arbitrária**), `created_at`). Migration própria.
- **Geração**: 1 token por `Offer` (reaproveitável entre missões que
  apontam pro mesmo anúncio) ou 1 por envio — decisão de implementação;
  reaproveitar por `Offer` reduz linhas na tabela e é consistente com
  `Offer` já ser a identidade estável do anúncio.
- **Colisão**: checagem de unicidade no `INSERT` (mesmo padrão já usado
  no projeto para outros identificadores gerados, ex.: tokens de ação
  de credencial da TASK-061).
- **Expiração/limpeza**: a decidir — pode nunca expirar (o link deve
  continuar funcionando enquanto o histórico da missão for consultável)
  ou expirar depois de um tempo generoso; se expirar, precisa de
  rotina de limpeza (fora do escopo de automação da V1 conforme
  `docs/OPERATIONS.md`, seguiria o mesmo padrão manual documentado lá).
- **Destino/segurança — requisito crítico, já reforçado pelo usuário**:
  a rota de redirect **só pode aceitar o `token`**, nunca uma URL
  arbitrária via query string — o destino vem exclusivamente de uma
  consulta ao banco (`token → offer.url`, ambos já validados e
  originados pelo pipeline de coleta). **Nunca implementar algo como
  `/r?url=...`** — isso seria um open redirect explorável para phishing,
  proibido explicitamente.
- **Abuso/phishing**: mitigado por construção, já que o destino nunca é
  fornecido pelo requisitante — só residual se um `Offer.url` malicioso
  chegasse a ser persistido, o que já seria um problema anterior a esta
  TASK (a validação de URL na coleta já existe e não muda aqui).
- **Ownership**: não parece necessário — o link curto representa uma
  oferta pública de loja, não um dado privado do usuário; a decidir se
  o usuário achar que deveria haver algum controle de acesso.
- **Métricas/cache**: opcional, fora do essencial — pode ser adicionado
  depois sem mudar o desenho (ex.: contagem de cliques via um campo
  incremental).
- **Disponibilidade**: a rota de redirect roda na `api`, que já tem
  `depends_on: database (healthy)`; nenhuma dependência nova de
  infraestrutura.

**Opção externa** (encurtador de terceiro): mencionada só para registro
— **não é a preferência do usuário**, e introduz dependência de
disponibilidade/confiança em serviço externo que a solução própria não
tem. Não desenvolvida em detalhe aqui, dado que o próprio pedido já
sinaliza preferência pela solução própria.

**Consideração arquitetural nova**: uma rota de redirect na `api`
adiciona um **quarto router HTTP** ao projeto (hoje só
`telegram`/`health`/`authentication`, confirmado na auditoria da Etapa
1). É uma leitura simples (`token` → `url`, sem transação longa, sem
IA, sem Telegram) — de baixo risco para a classe de bug da TASK-079,
mas registrado aqui para manter a auditoria daquela etapa atualizada
quando esta TASK for implementada.

## 5.E — Template proposto (baseado nos ícones já usados no projeto)

Reaproveitando o padrão visual já existente em `notifications.py`
(`🏪`/`💰`/`🔗` já usados hoje), sem inventar convenção nova:

```
1️⃣ {display_name}
🏪 {store.name}
💰 {amount formatado, format_money existente}
🚚 {regra de frete já existente, inalterada}
🔗 {link curto da oferta}
```

Enviado via `sendPhoto` (5.C) com essa string como `caption` quando há
imagem; via `sendMessage` (formato idêntico, sem a foto) quando não há
ou o envio da foto falha. **Esta TASK não muda a regra de frete/preço
já existente** (`amount` vs `total_amount`, TASK-075) — só reorganiza a
apresentação.

## Fora de escopo

- Regras de frete/comparação de preço (TASK-075) — inalteradas.
- Critério de ranking/quantas ofertas entram na pré-lista (TASK-068) —
  inalterado, só a forma de entrega de cada item já decidido.
- Encurtador de terceiro como solução definitiva.
- Qualquer mudança em `classify_offer_relevance`/normalização de título.

## Critérios de aceite

1. Cada oferta enviada ao usuário chega como uma unidade visual própria
   (uma mensagem `sendPhoto` ou, na ausência de imagem/falha, uma
   `sendMessage` equivalente) — nunca múltiplas ofertas concatenadas
   numa mensagem só.
2. Nenhuma URL completa de loja aparece na mensagem — sempre o link
   curto.
3. O endpoint de redirect nunca aceita destino arbitrário — só resolve
   `token` já persistido pelo próprio pipeline; teste dedicado tentando
   um destino externo via parâmetro deve falhar/ser rejeitado.
4. Ausência de imagem (extração falhou ou loja não expõe) nunca impede
   o envio da oferta.
5. Ordem de exibição preserva exatamente o ranking já decidido pelo
   pipeline existente (mesmo teste de regressão da TASK-068/075, sem
   alteração de asserção sobre qual oferta é "a melhor").
6. Nenhuma mudança de comportamento em frete/preço/classificação.
7. Pipeline oficial completo aprovado antes de qualquer commit.

## Impacto em banco/migration

Esperado, se a opção própria de link curto for escolhida: nova tabela
(`offer_short_links` ou nome equivalente) + migration. Se `image_url`
for persistida em `Offer`: coluna nova nullable, migration adicional
metadata-only (mesmo padrão de baixo risco já usado na TASK-075 para
`mission_criteria.model`).
