# Consumo de Eventos

A TASK-044 implementa a fronteira durável de consumo dos eventos publicados
pela TASK-043. O contrato oferece entrega **at-least-once por consumidor**:
uma falha pode ser repetida quantas vezes forem necessárias; o primeiro
registro de sucesso torna o evento inelegível somente para aquele consumidor.

## Contratos

`claim_unconsumed_events(session, consumer_name, limit)` seleciona eventos
que ainda não possuem tentativa `succeeded` do consumidor, em ordem por
`recorded_at` e `id`. A consulta usa `FOR UPDATE SKIP LOCKED`, permitindo que
transações concorrentes trabalhem em lotes distintos sem espera nem dupla
reivindicação enquanto o lock estiver aberto. `limit` aceita valores de 1 a
1000.

`record_consumption_attempt(...)` adiciona uma linha append-only com resultado
`succeeded` ou `failed`. Falhas exigem um `failure_code` estável em snake_case;
sucessos não aceitam código de falha. O horário `attempted_at` deve ser
consciente de fuso.

Os dois serviços recebem uma `Session` existente, executam `flush` quando
necessário e **não fazem commit**. O chamador deve manter a mesma transação
aberta durante reivindicação, processamento e registro do resultado; um commit
ou rollback antecipado libera o lock e encerra a garantia concorrente daquele
ciclo.

## Persistência

`event_consumption_attempts` referencia `events` com `ON DELETE RESTRICT` e
armazena `consumer_name`, `outcome`, `failure_code` e `attempted_at`. Um índice
parcial em `(consumer_name, event_id)` para linhas bem-sucedidas sustenta a
consulta de elegibilidade. Trigger PostgreSQL rejeita `UPDATE` e `DELETE`,
preservando todas as tentativas, inclusive falhas repetidas.

## Limites deliberados

- Não existe worker ou consumidor concreto nesta tarefa.
- Não há backoff, limite de tentativas, dead-letter queue ou scheduler.
- Não há promessa exactly-once; efeitos externos devem tolerar repetição.
- Não há integração com Telegram nem persistência de `chat_id`.
- Detecção e publicação de novos fatos continuam responsabilidades separadas.

Esses limites evitam antecipar a TASK-036 e as tarefas operacionais futuras.
