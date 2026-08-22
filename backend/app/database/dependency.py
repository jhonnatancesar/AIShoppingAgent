"""Sessão de banco por requisição para rotas FastAPI."""

from collections.abc import AsyncIterator, Iterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.database.session import (
    create_async_session_factory,
    create_database_engine,
    create_session_factory,
    create_telegram_async_database_engine,
)


@lru_cache
def _get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(create_database_engine())


def get_session() -> Iterator[Session]:
    """Abre uma sessão, comita no sucesso e desfaz em qualquer exceção."""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@lru_cache
def get_telegram_async_engine() -> AsyncEngine:
    """Engine assíncrono dedicado ao webhook Telegram (extensão da
    TASK-079), com processo-lifetime singleton -- mesma convenção de
    `_get_session_factory` acima. Exposto (sem `_`) porque também é usado
    diretamente por `app.telegram.concurrency.user_serialization_lock`,
    que precisa do engine para abrir sua própria conexão dedicada ao
    advisory lock."""
    return create_telegram_async_database_engine()


@lru_cache
def _get_telegram_async_session_factory() -> async_sessionmaker[AsyncSession]:
    return create_async_session_factory(get_telegram_async_engine())


async def get_telegram_async_session() -> AsyncIterator[AsyncSession]:
    """Abre uma `AsyncSession` dedicada ao webhook Telegram (extensão da
    TASK-079) -- ao contrário de `get_session`, NÃO comita automaticamente
    no sucesso: o próprio handler controla o limite exato de cada
    transação (Fase A/C), porque o corpo da requisição contém awaits de
    I/O externo (IA, Telegram) entre elas e nunca pode manter uma
    transação aberta durante esses awaits (causa raiz do autodeadlock,
    ver docs/tasks/TASK-079.md). Esta dependência só garante que qualquer
    transação esquecida/pendente seja desfeita e a sessão fechada ao
    final -- rede de segurança, não a correção em si.
    """
    session = _get_telegram_async_session_factory()()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()


@lru_cache
def _get_web_async_session_factory() -> async_sessionmaker[AsyncSession]:
    # Reaproveita o mesmo engine assíncrono de processo do webhook Telegram
    # (`get_telegram_async_engine`, já com os timeouts de banco aplicados)
    # -- não cria um pool de conexões novo só para a aplicação web.
    return create_async_session_factory(get_telegram_async_engine())


async def get_web_async_session() -> AsyncIterator[AsyncSession]:
    """Abre uma `AsyncSession` para rotas assíncronas da aplicação web
    (TASK-092) -- comita automaticamente no sucesso e desfaz em qualquer
    exceção, como `get_session` (a versão síncrona). Diferente do webhook
    Telegram, os endpoints da aplicação web não têm nenhum `await` de I/O
    externo (IA, Telegram) no meio da transação -- não existe aqui o
    autodeadlock que `get_telegram_async_session` evita com controle
    manual de fase, então uma única transação por requisição é segura."""
    session = _get_web_async_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
