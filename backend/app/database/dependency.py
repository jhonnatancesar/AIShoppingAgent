"""Sessão de banco por requisição para rotas FastAPI."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.database.session import create_database_engine, create_session_factory


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
