"""Serialização por usuário do processamento de mensagens do Telegram.

Extensão da TASK-079 para o webhook: duas mensagens do mesmo usuário nunca
podem ser processadas concorrentemente. O motivo é que o estado de conversa
multi-turno (`User.pending_intent`, fluxos de edição/criação de missão,
`User.registration_step`) é lido no início do processamento de uma mensagem
e só é persistido no fim -- se a mensagem B começar antes de A terminar, B
pode ler um estado que A ainda não gravou, ou as duas podem sobrescrever o
resultado uma da outra.

A chave do lock é o `telegram_user_id` numérico do `Update` recebido, não o
`User.id` (UUID) interno -- está disponível assim que o payload chega, antes
de qualquer consulta ao banco (inclusive antes de `authenticate_telegram_user`,
que pode até criar o `User` no primeiro contato). Se a chave dependesse de um
`User.id` já resolvido, seria preciso ler o usuário primeiro e só then
adquirir o lock, abrindo uma janela real de corrida: outra mensagem do
mesmo usuário poderia terminar e persistir seu estado nesse intervalo, e a
leitura já feita ficaria desatualizada. Travar pela identidade do Telegram
elimina essa janela por completo.

A garantia precisa valer entre processos (múltiplas instâncias futuras da
API), então não pode depender de memória local (`asyncio.Lock` só serializa
dentro de um processo). Também não pode manter uma transação aberta nem um
`FOR UPDATE` de linha durante a espera, porque o corpo protegido inclui
chamadas de I/O externo (IA, Telegram) -- exatamente o padrão que causou o
autodeadlock original (ver docs/tasks/TASK-079.md).

A solução é `pg_advisory_lock`, uma primitiva nativa do Postgres com escopo
de *sessão* (conexão), não de transação: adquirida fora de qualquer
transação (a conexão desta chave roda em AUTOCOMMIT, então nunca fica
"idle in transaction" enquanto aguardamos IA/Telegram) e liberada
explicitamente ao final. Usa-se `pg_try_advisory_lock` em polling, não
`pg_advisory_lock` bloqueante, para nunca manter uma única instrução SQL
presa no servidor por mais tempo que os airbags de timeout desta engine
(`telegram_statement_timeout_seconds`) permitiriam.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_POLL_INITIAL_SECONDS = 0.02
_POLL_MAX_SECONDS = 0.2

# Bigint do Postgres é assinado, 64 bits; o `user_id` do Telegram já é um
# inteiro positivo que cabe folgadamente nesse intervalo (hoje na casa de
# 10 dígitos). Validar em vez de mascarar/hashear evita esconder um valor
# inesperado (ex.: um `Update` corrompido) atrás de uma chave de lock que
# pareceria válida.
_MAX_BIGINT = 2**63 - 1


def advisory_lock_key(telegram_user_id: int) -> int:
    """Valida que o `telegram_user_id` cabe no `bigint` usado pelo advisory
    lock do Postgres."""
    if not 0 < telegram_user_id <= _MAX_BIGINT:
        raise ValueError("telegram_user_id fora do intervalo de bigint")
    return telegram_user_id


@asynccontextmanager
async def user_serialization_lock(
    engine: AsyncEngine, telegram_user_id: int
) -> AsyncIterator[None]:
    """Serializa o processamento de mensagens do mesmo `telegram_user_id` --
    entre requisições concorrentes no mesmo processo e entre processos
    diferentes.

    Mantém uma conexão dedicada, em AUTOCOMMIT, com o advisory lock do
    usuário adquirido durante todo o bloco (inclusive durante awaits de I/O
    externo). A conexão só é devolvida ao pool ao final, com o lock já
    liberado -- por isso o corpo do bloco deve ser mantido curto o
    suficiente para caber no `telegram_message_deadline_seconds` global.
    """
    key = advisory_lock_key(telegram_user_id)
    connection = await engine.connect()
    try:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        delay = _POLL_INITIAL_SECONDS
        while True:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
            if acquired:
                break
            await asyncio.sleep(delay)
            delay = min(_POLL_MAX_SECONDS, delay * 2)
        try:
            yield
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            )
    finally:
        await connection.close()
