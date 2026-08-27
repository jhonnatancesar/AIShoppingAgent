"""Add market price assessment and mission/product alert checkpoint (TASK-113).

Revision ID: 20260827_0001
Revises: 20260826_0001

`market_price_assessments` (TASK-113 §33.1-§33.3): uma linha mutável por
`product_id`, single-flight crash-safe via claim atômico feito em
código (`INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`, ver
`app.market_research.service`), nunca por esta migration.

`mission_product_alert_state` (§33.4-§33.6): checkpoint de alerta por
`(mission_id, product_id)`.

Backfill (§33.24, correção pós-plano ponto 4): reconstrução
DETERMINÍSTICA a partir do histórico real de `Event`s
(`price.decreased.v1`/`price.target_reached.v1`, ambos com `mission_id`
sempre preenchido -- único call site de `evaluate_price_alerts` é
`app.collection.orchestration._persist_phase_c`, confirmado antes desta
migration) -- nunca a suposição rejeitada "último preço conhecido =
preço alertado". `payload->>'offer_id'`/`payload->>'current_total'` são
sempre válidos (payload validado por dataclass antes de serializar,
`app.events.service._serialize_payload`); junta com `offers.product_id`
(FK RESTRICT, nunca apagada) para obter o produto. `best_notified_
amount` = MIN(current_total) entre todos os eventos de alerta daquela
Mission+Product; `last_notified_amount`/`last_notified_at`/`last_alert_
event_id` = o evento de `occurred_at` mais recente. `rearmed_at` nasce
`NULL` (conservador -- sem o preço completo entre alertas, não é
possível reconstruir se uma subida material já ocorreu; o mecanismo de
REARM volta a funcionar normalmente a partir da próxima coleta real).
Sem esse backfill, toda Mission pareceria "nunca alertada" (caminho A)
no primeiro ciclo após o deploy, o que reabriria o próprio bug que a
TASK-113 corrige (potencial tempestade de alertas revisitando preços já
notificados).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0001"
down_revision: str | None = "20260826_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "pending",
        "processing",
        "ready",
        "failed",
        name="market_assessment_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    classification = postgresql.ENUM(
        "excellent_deal",
        "good_deal",
        "normal_price",
        "insufficient_evidence",
        name="market_price_classification",
        create_type=False,
    )
    classification.create(op.get_bind(), checkfirst=True)
    confidence = postgresql.ENUM(
        "low",
        "medium",
        "high",
        name="assessment_confidence",
        create_type=False,
    )
    confidence.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "market_price_assessments",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            status,
            nullable=False,
            server_default="processing",
        ),
        sa.Column("reference_price", sa.Numeric(19, 4), nullable=False),
        sa.Column("reference_currency", sa.CHAR(3), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("classification", classification, nullable=True),
        sa.Column("market_low", sa.Numeric(19, 4), nullable=True),
        sa.Column("market_high", sa.Numeric(19, 4), nullable=True),
        sa.Column("historical_low_external", sa.Numeric(19, 4), nullable=True),
        sa.Column("historical_low_source", sa.String(length=2000), nullable=True),
        sa.Column("historical_low_observed_at", sa.Date(), nullable=True),
        sa.Column("confidence", confidence, nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "failure_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "historical_low_source IS NOT NULL OR historical_low_external IS NULL",
            name="ck_market_price_assessments_historical_low_source_required",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_market_price_assessments_failure_count_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_market_price_assessments_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_market_price_assessments_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "product_id", name=op.f("pk_market_price_assessments")
        ),
    )
    # Recuperação de lease expirado (§33.3, mesmo espírito de
    # `ix_shared_fan_out_tasks_processing_claimed_at`) -- sem isto, um
    # worker morto em PROCESSING só seria achado por sequential scan.
    op.create_index(
        "ix_market_price_assessments_processing_lease",
        "market_price_assessments",
        ["lease_until"],
        postgresql_where=sa.text("status = 'processing'"),
    )

    op.create_table(
        "mission_product_alert_state",
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("best_notified_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("best_notified_currency", sa.CHAR(3), nullable=False),
        sa.Column("last_notified_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rearmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_alert_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_product_alert_state_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_mission_product_alert_state_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_alert_event_id"],
            ["events.id"],
            name=op.f(
                "fk_mission_product_alert_state_last_alert_event_id_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "mission_id", "product_id", name=op.f("pk_mission_product_alert_state")
        ),
    )
    op.create_index(
        "ix_mission_product_alert_state_product_id",
        "mission_product_alert_state",
        ["product_id"],
    )

    # Backfill determinístico a partir do histórico real de Event -- ver
    # docstring do módulo. Sem isto, toda Mission entraria no evaluator
    # novo como "nunca alertada" (caminho A), reabrindo o próprio bug que
    # esta TASK corrige.
    op.execute(
        sa.text(
            """
            WITH alert_events AS (
                SELECT
                    e.mission_id AS mission_id,
                    o.product_id AS product_id,
                    e.id AS event_id,
                    (e.payload ->> 'current_total')::numeric(19, 4) AS amount,
                    e.payload ->> 'currency' AS currency,
                    e.occurred_at AS occurred_at
                FROM events e
                JOIN offers o ON o.id = (e.payload ->> 'offer_id')::uuid
                WHERE e.event_type IN ('price.decreased.v1', 'price.target_reached.v1')
                  AND e.mission_id IS NOT NULL
            ),
            best AS (
                SELECT DISTINCT ON (mission_id, product_id)
                    mission_id,
                    product_id,
                    amount AS best_notified_amount,
                    currency AS best_notified_currency
                FROM alert_events
                ORDER BY mission_id, product_id, amount ASC, occurred_at ASC
            ),
            last_alert AS (
                SELECT DISTINCT ON (mission_id, product_id)
                    mission_id,
                    product_id,
                    amount AS last_notified_amount,
                    occurred_at AS last_notified_at,
                    event_id AS last_alert_event_id
                FROM alert_events
                ORDER BY mission_id, product_id, occurred_at DESC, amount ASC
            )
            INSERT INTO mission_product_alert_state (
                mission_id, product_id, best_notified_amount, best_notified_currency,
                last_notified_amount, last_notified_at, rearmed_at,
                last_alert_event_id, updated_at
            )
            SELECT
                b.mission_id, b.product_id, b.best_notified_amount,
                b.best_notified_currency, l.last_notified_amount,
                l.last_notified_at, NULL, l.last_alert_event_id, now()
            FROM best b
            JOIN last_alert l
                ON l.mission_id = b.mission_id AND l.product_id = b.product_id
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mission_product_alert_state_product_id",
        table_name="mission_product_alert_state",
    )
    op.drop_table("mission_product_alert_state")
    op.drop_index(
        "ix_market_price_assessments_processing_lease",
        table_name="market_price_assessments",
    )
    op.drop_table("market_price_assessments")
    postgresql.ENUM(name="assessment_confidence").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="market_price_classification").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(name="market_assessment_status").drop(
        op.get_bind(), checkfirst=True
    )
