"""Cria a trilha de auditoria.

Revision ID: 20260802_0005
Revises: 20260802_0004
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0005"
down_revision: str | None = "20260802_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria registros append-only para ações relevantes."""
    op.create_table(
        "audit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(actor_type) <> ''",
            name="ck_audit_entries_actor_type_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(action) <> ''",
            name="ck_audit_entries_action_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(resource_type) <> ''",
            name="ck_audit_entries_resource_type_not_blank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_audit_entries_metadata_object",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_entries_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entries")),
    )
    op.create_index(
        "ix_audit_entries_resource_history",
        "audit_entries",
        ["resource_type", "resource_id", "created_at", "id"],
    )
    op.create_index(
        "ix_audit_entries_actor_history",
        "audit_entries",
        ["actor_id", "created_at"],
        postgresql_where=sa.text("actor_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_audit_entries_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_entries is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_entries_append_only
        BEFORE UPDATE OR DELETE ON audit_entries
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_entries_mutation()
        """
    )


def downgrade() -> None:
    """Remove somente a trilha introduzida por esta revisão."""
    op.execute("DROP TRIGGER trg_audit_entries_append_only ON audit_entries")
    op.drop_index("ix_audit_entries_actor_history", table_name="audit_entries")
    op.drop_index("ix_audit_entries_resource_history", table_name="audit_entries")
    op.drop_table("audit_entries")
    op.execute("DROP FUNCTION prevent_audit_entries_mutation()")
