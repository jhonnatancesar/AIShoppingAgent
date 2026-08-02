"""Metadata declarativa compartilhada pelos modelos persistentes."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base única para modelos e autogeração de migrações."""
