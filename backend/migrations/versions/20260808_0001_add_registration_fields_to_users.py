"""Adiciona campos de cadastro inicial a users.

Revision ID: 20260808_0001
Revises: 20260807_0002
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0001"
down_revision: str | None = "20260807_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Adiciona os campos do cadastro inicial (TASK-060), todos opcionais.

    Nenhum dado de autenticação real (senha, token) é introduzido aqui —
    isso permanece reservado à TASK-061 (`DEC-019`).
    """
    op.add_column("users", sa.Column("username", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_check_constraint(
        "ck_users_username_not_blank",
        "users",
        "username IS NULL OR btrim(username) <> ''",
    )
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.create_check_constraint(
        "ck_users_email_not_blank",
        "users",
        "email IS NULL OR btrim(email) <> ''",
    )
    op.add_column(
        "users",
        sa.Column(
            "favorite_stores",
            sa.ARRAY(sa.String(length=32)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "preferred_categories",
            sa.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "users",
        sa.Column("registration_step", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Remove somente o que esta revisão introduziu."""
    op.drop_column("users", "registration_step")
    op.drop_column("users", "preferred_categories")
    op.drop_column("users", "favorite_stores")
    op.drop_constraint("ck_users_email_not_blank", "users", type_="check")
    op.drop_column("users", "email")
    op.drop_constraint("ck_users_username_not_blank", "users", type_="check")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
