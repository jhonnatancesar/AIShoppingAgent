"""Add offer images, short links and per-part delivery checkpoints.

Revision ID: 20260816_0001
Revises: 20260811_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260816_0001"
down_revision: str | None = "20260811_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("image_url", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_offers_image_url_http",
        "offers",
        "image_url IS NULL OR image_url ~* "
        "'^https?://[^/@?#[:space:]]+([/?#]|$)'",
    )
    op.create_table(
        "offer_short_links",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(token) <> ''", name="ck_offer_short_links_token_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"], ["offers.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("token"),
        sa.UniqueConstraint("offer_id", name="uq_offer_short_links_offer_id"),
    )
    op.create_table(
        "event_delivery_checkpoints",
        sa.Column("consumer_name", sa.String(length=64), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_part", sa.Integer(), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(consumer_name) <> ''",
            name="ck_event_delivery_checkpoints_consumer_not_blank",
        ),
        sa.CheckConstraint(
            "message_part >= 0",
            name="ck_event_delivery_checkpoints_part_non_negative",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint(
            "consumer_name", "event_id", "offer_id", "message_part"
        ),
    )
    op.create_index(
        "ix_event_delivery_checkpoints_event",
        "event_delivery_checkpoints",
        ["consumer_name", "event_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_delivery_checkpoints_event",
        table_name="event_delivery_checkpoints",
    )
    op.drop_table("event_delivery_checkpoints")
    op.drop_table("offer_short_links")
    op.drop_constraint("ck_offers_image_url_http", "offers", type_="check")
    op.drop_column("offers", "image_url")
