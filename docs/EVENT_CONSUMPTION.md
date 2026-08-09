# Consumo de Eventos

A TASK-044 implementa a fronteira durável de consumo dos eventos publicados
pela TASK-043. O contrato oferece entrega **at-least-once por consumidor**:
uma falha transitória pode ser repetida até o limite configurado; o primeiro
resultado terminal (`succeeded`, `skipped` ou `dead_lettered`) torna o evento
inelegível somente para aquele consumidor.

## Contratos

`claim_unconsumed_events(session, consumer_name, limit)` seleciona eventos
que ainda não possuem tentativa terminal do consumidor, em ordem por
`recorded_at` e `id`. A consulta usa `FOR UPDATE SKIP LOCKED`, permitindo que
transações concorrentes trabalhem em lotes distintos sem espera nem dupla
reivindicação enquanto o lock estiver aberto. `limit` aceita valores de 1 a
1000.

`record_consumption_attempt(...)` adiciona uma linha append-only com resultado
`succeeded`, `failed`, `skipped` ou `dead_lettered`. Falhas transitórias exigem
`failure_code` e `next_retry_at`; dead letter exige somente o código estável.
`succeeded`/`skipped` não aceitam código. `skipped` foi
adicionado pela TASK-037 para registrar supressão deliberada por preferência,
sem retry. O horário `attempted_at` deve ser consciente de fuso.

Os dois serviços recebem uma `Session` existente, executam `flush` quando
necessário e **não fazem commit**. O chamador deve manter a mesma transação
aberta durante reivindicação, processamento e registro do resultado; um commit
ou rollback antecipado libera o lock e encerra a garantia concorrente daquele
ciclo.

## Persistência

`event_consumption_attempts` referencia `events` com `ON DELETE RESTRICT` e
armazena `consumer_name`, `outcome`, `failure_code`, `attempted_at` e
`next_retry_at`. Um índice
parcial em `(consumer_name, event_id)` para linhas terminais sustenta a
consulta de elegibilidade. Trigger PostgreSQL rejeita `UPDATE` e `DELETE`,
preservando todas as tentativas, inclusive falhas repetidas.

## Limites deliberados

- Não existe worker ou consumidor concreto nesta tarefa.
- Backoff e dead letter são fatos no PostgreSQL, não uma fila separada.
- Não há promessa exactly-once; efeitos externos devem tolerar repetição.
- Não há integração com Telegram nem persistência de `chat_id`.
- Detecção e publicação de novos fatos continuam responsabilidades separadas.

## Consumidor Telegram (TASK-036)

`telegram_price_alerts_v1` filtra somente `price.decreased.v1` e
`price.target_reached.v1`. Ele resolve `Event.mission_id` até o proprietário e
seu `telegram_chat_id` privado, envia pela Bot API e registra o desfecho na
mesma transação do lock. Destino ausente/inativo e falha transitória produzem
retry agendado. Payload inválido, rejeição permanente ou entrega ambígua vão
para dead letter; esta última não é repetida cegamente porque o Telegram pode
ter aceitado o envio.

O processo contínuo é `python -m app.telegram.worker`; no Compose, o serviço
`telegram_notifier` executa esse módulo. Como a garantia continua at-least-once,
uma entrega aceita pelo Telegram seguida de rollback do banco pode ser repetida.
Mensagens devem, portanto, tolerar duplicação.

Depois da TASK-049, o worker só reivindica `failed` quando `next_retry_at`
venceu, encerra após cinco tentativas e nunca dorme mantendo transação ou lock.
Falha inesperada faz rollback e o processo aplica backoff fora da transação.
Detalhes estão em `docs/RESILIENCE.md`.

## Preferências de notificações (TASK-037)

Antes do envio, o consumidor consulta a preferência correspondente ao tipo do
evento. Queda de preço e preço-alvo são independentes e começam ativados. Se a
opção estiver desativada, nenhuma chamada à Bot API ocorre e a tentativa é
registrada como `skipped`, terminal e sem `failure_code`. Reativar a opção não
torna eventos antigos elegíveis; somente eventos novos voltam ao fluxo normal.

## Consumidor de autenticação

`telegram_auth_notifications_v1` reivindica somente os três eventos
`authentication.*` documentados no catálogo. Ele resolve o usuário ou a sessão
exata, valida a identidade persistida e envia confirmações e avisos ao chat
privado. Esse fluxo não consulta preferências de preço. Sessão revogada ou
aviso prévio já vencido termina como `skipped`, sem retry; falhas de entrega
seguem o mesmo contrato sanitizado de retry/dead letter do consumidor de
preços.

O worker publica eventos de expiração e os consome em transações separadas. A
publicação usa `FOR UPDATE SKIP LOCKED` e dois marcadores booleanos na sessão,
atualizados atomicamente com o evento, para impedir eventos duplicados em
concorrência e após restart.
