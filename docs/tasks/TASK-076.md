# TASK-076 — Enriquecer logs de falha dos providers de coleta

Status: **Planejada e aprovada** (2026-08-12) — decisão arquitetural
tomada pelo usuário (ver "Decisão aprovada" abaixo). Ainda **não
implementada**; aguarda início da implementação.

Dependência: nenhuma. Relacionada à correção de readiness da Pichau
(`docs/tasks/TASK-075.md`, seção "Correção pós-implementação"), que expôs
a lacuna descrita abaixo durante o diagnóstico real.

Versão alvo: `v1.0.6`.

## Problema

Durante o diagnóstico da falha real da Pichau (2026-08-11/12,
`docs/tasks/TASK-075.md`), a causa raiz só pôde ser confirmada reproduzindo
a falha isoladamente fora de produção — os logs de produção não traziam
informação suficiente para diagnosticar sozinhos.

Investigação do código confirmou exatamente onde a informação se perde.
Em `app/collection/orchestration.py::_process` (linhas 347-399), o bloco
`except Exception as error:` tem acesso total ao objeto da exceção
original — tipo, mensagem, `status` (quando aplicável) e traceback — mas
usa esse objeto só para alimentar duas funções puras que o reduzem a um
booleano e a uma string fechada de 5 valores:

```python
except Exception as error:
    failure_code = _failure_code(error)              # 1 de 5 strings fixas
    confirmed_block = _is_confirmed_external_block(error)  # bool
    ...
    logger.warning(
        "collection_source_failed",
        extra={
            "source_code": _safe_source(claim.source_code),
            "failure_code": failure_code,
        },
    )
```

Depois desse bloco, `error` sai de escopo — tipo, mensagem, `status` (ex.:
`ProviderNavigationError.status`, disponível mas nunca lido para o log) e
traceback nunca chegam a lugar nenhum: nem ao log, nem ao evento
`COLLECTION_FAILED_V1` (`_record_failure` só grava `failure_code`), nem a
nenhuma coluna do banco (`CollectionRun` não tem coluna de erro — só
`status`/`started_at`/`finished_at`).

Achado adicional relevante: mesmo quando o código já usa
`logger.exception(...)` (dois lugares neste mesmo arquivo:
`collection_integrity_failure` e `collection_failure_recording_failed`),
o `JsonFormatter` atual (`app/core/logging.py`) **não serializa o
traceback** — ele só acrescenta um campo `exception_type` quando
`record.exc_info` está presente; o texto formatado da pilha nunca é lido
nem incluído no JSON. Ou seja, hoje nenhum log estruturado do projeto
carrega traceback, independentemente do provider — não é um problema
isolado da coleta.

## Objetivo

Uma falha real de coleta em produção deve deixar informação técnica
suficiente (tipo da exceção, mensagem, status quando houver, e
idealmente traceback) para diagnosticar sem precisar reproduzir a falha
isoladamente — usando só dados já disponíveis no momento da falha, sem
scraping adicional, sem chamada externa nova, sem chamada de IA.

## Escopo

- Enriquecer o `extra` do `logger.warning("collection_source_failed", ...)`
  em `_process` com, no mínimo: `source_code`, `failure_code` (já
  existentes), tipo da exceção (`type(error).__name__`), mensagem
  sanitizada, `status` quando o atributo existir
  (`ProviderNavigationError`/`ProviderBlockedError`), etapa da coleta e
  traceback sanitizado.
- Capturar e formatar o traceback **localmente, só neste ponto** (dentro
  de `_process`), sem tocar no `JsonFormatter` compartilhado — decisão
  aprovada, ver "Decisão aprovada" abaixo.
- Mapear "etapa da coleta" a partir do **tipo da exceção já existente** —
  não é preciso instrumentar novos pontos de código: `ProviderNavigationError`
  ⇒ navegação; `ProviderBlockedError` ⇒ seletor/extração/possível bloqueio;
  `CollectionNormalizationError`/`CollectionContractError` ⇒ normalização;
  `IntegrityError` ⇒ persistência. Documentar esse mapeamento explicitamente
  no código (comentário ou pequena função), não inventar um novo conceito de
  "stage" paralelo.
- Confirmar quais mensagens de exceção são seguras para logar (ver
  "Segurança").
- Avaliar se reaproveita `_sanitize_json` (já existe em
  `orchestration.py`, usado hoje só para `provider_evidence`) para limitar
  tamanho de qualquer texto novo, em vez de criar sanitização paralela.
- Escolher nomes de campo que **não colidam** com o redator automático do
  `JsonFormatter` (ver "Segurança" — `SENSITIVE_FIELD_FRAGMENTS` remove
  silenciosamente qualquer campo cujo nome contenha `"url"`, `"query"`,
  `"message"` etc.).
- Teste novo cobrindo o conteúdo do log (`caplog`), hoje inexistente —
  `collection_source_failed` não tem nenhuma cobertura direta.
- Avaliar volume/retenção de log (ver "Requisitos técnicos").

## Fora de escopo

- Regra de retry, `RetryPolicy`, número de tentativas — inalterados.
- Comportamento dos providers (`PlaywrightStoreProvider`,
  `PichauProvider` etc.) — inalterado.
- Taxonomia/classificação de falha (`_failure_code`, os 5 valores fixos,
  `_is_confirmed_external_block`) — inalterada; esta TASK só enriquece o
  que é logado a partir da mesma classificação já existente.
- Filtros determinísticos, persistência de ofertas, IA, Telegram,
  funcionamento normal da coleta — inalterados.
- Nova coluna/tabela no banco para detalhe de falha — **não é o pedido
  desta TASK** (o pedido é sobre logs); se o usuário quiser também
  persistir detalhe de falha de forma consultável no banco, isso é uma
  decisão separada, com sua própria migration/política de retenção, fora
  desta TASK.
- Métrica Prometheus nova (`observe_resilience_event("store", "failure")`,
  já suportado pelo enum `RESILIENCE_EVENTS` mas nunca chamado a partir de
  `orchestration.py`) — mencionada como achado relacionado, não incluída
  no escopo a menos que o usuário peça explicitamente.

## Requisitos funcionais

Ao falhar uma coleta, o log `collection_source_failed` deve registrar,
quando disponível, exatamente os campos aprovados pelo usuário:
1. `source_code`/provider (já existe, allowlisted).
2. `failure_code` (já existe, 5 valores fechados).
3. Tipo da exceção original (`type(error).__name__`).
4. Mensagem sanitizada — só depois de confirmada seguramente logável
   (ver "Segurança"); se houver qualquer dúvida sobre um tipo
   específico, omitir a mensagem desse tipo e manter os demais campos.
5. `status` HTTP quando disponível na exceção (hoje descartado mesmo
   existindo em `ProviderNavigationError`/`ProviderBlockedError`).
6. Etapa da coleta (derivada do tipo da exceção, ver "Escopo").
7. Traceback sanitizado, capturado localmente no ponto de falha (ver
   "Decisão aprovada").

Nunca registrar a exceção bruta do Playwright — só as exceções de
domínio já sanitizadas (ver "Segurança").

## Requisitos técnicos

- O ponto de captura é o `except Exception as error:` já existente em
  `_process` (`orchestration.py:375`) — nenhum novo `try/except` precisa
  ser introduzido nos providers.
- Nomes de campo sugeridos para não colidir com
  `SENSITIVE_FIELD_FRAGMENTS`: `error_class` (não `error_message`/
  `error_type` sozinho é seguro, mas `message` como nome de campo
  isolado colide com `STANDARD_RECORD_ATTRIBUTES` e seria descartado
  mesmo sem estar na lista de fragmentos sensíveis — usar
  `error_detail` ou `error_text` em vez de `error_message`),
  `provider_status`, `failure_stage`. Validar cada nome escolhido contra
  `SENSITIVE_FIELD_FRAGMENTS` e `STANDARD_RECORD_ATTRIBUTES`
  explicitamente antes de implementar, não só por inspeção visual.
- Reaproveitar `_sanitize_json`/o mesmo princípio de truncamento (já usado
  para `provider_evidence`, limite de 1000 caracteres em
  `RawCollectedOffer.evidence`) para limitar o tamanho de qualquer texto
  novo (mensagem, traceback) — evita crescimento descontrolado por
  exceções com mensagens muito longas.
- Volume/retenção: os únicos eventos que passam por este caminho são
  falhas reais de coleta (não é um log de alto volume por natureza — só
  dispara quando `_process` cai no `except`). Ainda assim, limitar
  traceback a um número máximo de frames/caracteres evita que uma cadeia
  de exceções encadeadas (`raise ... from ...`) produza um log
  desproporcional.

### Decisão aprovada (2026-08-12)

O usuário decidiu explicitamente **não alterar o `JsonFormatter`
compartilhado** (`app/core/logging.py`) — o problema observado é
específico da coleta, e o projeto não deve ampliar o comportamento de
logging global sem necessidade. Escopo fica no menor possível.

Implementação: capturar e formatar o traceback **manualmente, só no
ponto de `collection_source_failed`** (dentro de `_process`, ex.:
`traceback.format_exception(type(error), error, error.__traceback__)`,
truncado/limitado, passado como campo `extra` já sanitizado). O
`JsonFormatter` continua exatamente como está — nenhum outro logger do
projeto (autenticação, Telegram, IA, etc.) ganha traceback como efeito
colateral desta TASK. Se o projeto decidir no futuro generalizar
traceback para outros loggers, isso é uma decisão própria, separada
desta.

## Segurança

Confirmado por leitura do código (não suposição):

- `ProviderNavigationError`/`ProviderBlockedError`/`ProviderCircuitOpenError`
  (`app/collection/errors.py`) são construídas só com `source_code`
  (string curta de uma allowlist fechada) e `status` (int HTTP ou
  `None`) — a mensagem nunca inclui URL, query string, termo de busca ou
  qualquer dado do usuário. **Seguras para logar como estão hoje.**
- A exceção bruta do Playwright (`PlaywrightTimeoutError`/`PlaywrightError`),
  que **pode** conter a URL completa navegada na própria mensagem, já é
  descartada deliberadamente hoje (`raise ProviderNavigationError(...)
  from None` em `providers/base.py`) antes de chegar à orquestração —
  essa TASK **não deve reverter isso**: nunca logar `str()` da exceção
  bruta do Playwright, só da exceção já sanitizada
  (`ProviderNavigationError`/`ProviderBlockedError`).
- `CollectionNormalizationError`/`CollectionContractError` — mensagens
  genéricas sobre nome de campo/formato de valor observadas no código
  lido, mas a implementação exata de `PriceNormalizer` (mensagens de erro
  de parsing de preço) não foi conferida linha a linha nesta investigação.
  **Antes de logar a mensagem desses dois tipos, confirmar explicitamente
  que nunca embutem texto bruto raspado sem limite de tamanho** — se
  houver dúvida, logar só tipo+etapa, omitir mensagem.
- O traceback capturado localmente deve usar exatamente o módulo
  `traceback` padrão do Python (`format_exception`, que não inclui
  valores de variáveis locais por padrão) — não adotar bibliotecas de
  "rich traceback" que expõem locais, isso vazaria qualquer segredo que
  esteja em uma variável local no momento da falha (ex.: um token
  passado como parâmetro em algum frame intermediário).
- Todos os campos novos devem ser validados contra
  `SENSITIVE_FIELD_FRAGMENTS`/`STANDARD_RECORD_ATTRIBUTES`
  (`app/core/logging.py`) antes de nomear — um campo mal nomeado
  simplesmente desaparece do log silenciosamente (falha silenciosa,
  pior que não tentar).

## Critérios de aceite

1. Uma falha real de qualquer provider produz um log
   `collection_source_failed` com `source_code`, `failure_code`, tipo da
   exceção, mensagem sanitizada, status (quando houver), etapa da coleta
   e traceback sanitizado — verificável sem reproduzir a falha de novo.
2. `app/core/logging.py` (`JsonFormatter`) permanece byte-a-byte
   inalterado — confirmado por diff, não só por não ter sido mencionado
   na implementação.
3. Nenhum campo novo desaparece silenciosamente por colisão com o
   redator de campos sensíveis (validado explicitamente, não por
   inspeção).
4. Nenhuma URL completa, termo de busca, token, senha, secret, cookie,
   header de autorização ou conteúdo de `.env`/`.secrets` aparece em
   nenhum log novo — confirmado por revisão de cada tipo de exceção
   logado.
5. `_failure_code`, `_is_confirmed_external_block`, retry, filtros,
   persistência de ofertas, IA e Telegram permanecem com comportamento
   idêntico ao de hoje (testes existentes continuam passando sem
   alteração de asserção).
6. Pipeline oficial completo aprovado (Gitleaks incluso).

## Testes esperados

- Teste novo com `caplog` (padrão já usado em `test_telegram_router.py`)
  confirmando que `collection_source_failed` carrega os campos novos para
  pelo menos um caso de cada tipo de exceção relevante
  (`ProviderNavigationError`, `ProviderBlockedError`,
  `ProviderCircuitOpenError`, `CollectionNormalizationError`).
- Teste confirmando que um campo com nome inadvertidamente "sensível" (ex.:
  algo contendo `"url"`) realmente desaparece do JSON final — documentando
  esse comportamento do formatter, não só evitando-o na implementação.
- Teste confirmando que `app/core/logging.py` não foi importado/alterado
  por este trabalho além do necessário para os testes existentes
  continuarem passando sem modificação — ou, no mínimo, revisão de diff
  confirmando zero mudança no arquivo.
- Teste confirmando que outros loggers do projeto (ex.: autenticação,
  Telegram) continuam sem traceback no JSON — a mudança é local à coleta,
  não deve vazar para os demais.
- Regressão completa de `tests/test_collection_orchestration.py` e
  `tests/integration/test_collection_orchestration.py` sem alteração de
  asserções existentes.

## Impacto em banco/migration

Nenhum planejado. A TASK é sobre logs, não sobre persistência — nenhuma
tabela ou coluna nova é necessária para atender ao pedido como
especificado. Se o usuário decidir, em algum momento, que quer também
persistir detalhe de falha de forma consultável no banco (não só em
log), isso deve ser tratado como uma TASK própria, com sua própria
análise de volume/retenção — não misturado a esta.
