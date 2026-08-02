# Transições persistentes de missão

A TASK-021 implementa o ciclo de vida definido em `docs/MISSION_SYSTEM.md` sem
expor API ou antecipar agenda, coleta e eventos.

## Histórico

Cada mudança aceita adiciona uma linha em `mission_transitions` com missão,
estado anterior e novo, comando, ator, motivo opcional e instante UTC. Chaves
estrangeiras usam `RESTRICT`, a consulta histórica possui ordenação determinística
por `(mission_id, transitioned_at, id)` e um trigger PostgreSQL rejeita `UPDATE` e
`DELETE`.

## Execução atômica

`transition_mission` bloqueia a missão com `SELECT FOR UPDATE`, compara
`expected_state_version` com a versão persistida e valida o comando contra o
estado atual. Na mesma transação do chamador, o serviço:

1. altera `status`;
2. incrementa `state_version`;
3. atualiza `updated_at`;
4. inclui o registro histórico;
5. executa `flush`, sem decidir o `commit` do chamador.

Ativação e retomada exigem critérios persistidos. Retomada rejeita prazo já
alcançado; expiração exige um prazo alcançado. Estados terminais não possuem
saída. Comandos concorrentes com a mesma versão são serializados pelo banco e
somente um pode ser aceito.

## Limites

Não há API, repositório genérico, agendamento, coleta, publicação de eventos ou
auditoria duplicada. Erros de domínio são internos ao módulo até que uma tarefa
de API defina sua representação HTTP.
