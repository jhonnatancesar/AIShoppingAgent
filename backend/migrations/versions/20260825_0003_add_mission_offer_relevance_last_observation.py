"""Add last_observation_id to mission_offer_relevance (TASK-112 fase 3A).

Revision ID: 20260825_0003
Revises: 20260825_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0003"
down_revision: str | None = "20260825_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TASK-112 (fase 3A): "previous" por Mission deixa de poder ser
    # derivado só de `CollectionRun.mission_id` -- uma coleta compartilhada
    # persiste a PriceObservation presa à CollectionRun do item (mission_id
    # NULL), nunca a de uma Mission específica. `last_observation_id`
    # registra, por (mission_id, offer_id), a última observação que ESSA
    # Mission já processou -- correto independente de quem coletou.
    op.add_column(
        "mission_offer_relevance",
        sa.Column(
            "last_observation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        op.f("fk_mission_offer_relevance_last_observation_id_price_observations"),
        "mission_offer_relevance",
        "price_observations",
        ["last_observation_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Backfill (correção de consistência, mesma rodada): toda linha
    # PRÉ-EXISTENTE de `mission_offer_relevance` fica com
    # `last_observation_id=NULL` por padrão -- sem isso, o fan-out
    # compartilhado trataria essa Mission como "nunca viu nenhuma
    # observação" na primeira vez que processasse essa oferta pelo
    # caminho novo, o que pode reacionar `PRICE_TARGET_REACHED` para uma
    # Mission que já tinha cruzado o alvo há muito tempo (a proteção
    # contra isso é justamente comparar contra o `previous` certo).
    #
    # Reconstrói o valor correto usando o MESMO dado que
    # `_persist_phase_a` já usava para achar o "previous" de cada Mission
    # antes desta coluna existir: a `PriceObservation` mais recente cujo
    # `collection_run_id` aponta para uma `CollectionRun` daquela mesma
    # Mission. Isso cobre 100% das linhas alcançáveis em produção hoje --
    # toda `mission_offer_relevance` existente foi criada exatamente por
    # esse caminho (`_persist_phase_a`/`_persist_phase_c`), nunca por
    # outro. `WHERE last_observation_id IS NULL` torna a operação
    # idempotente (segura de rodar de novo). Uma linha hipotética sem
    # nenhuma `PriceObservation` correspondente (nunca visto em produção)
    # ficaria `NULL` mesmo após o backfill -- fail-safe: tratada como
    # primeira observação, nunca gera falso "queda de preço"
    # (`PRICE_DECREASED` exige `previous is not None`); o único risco
    # residual nesse caso hipotético e inalcançável seria um possível
    # re-disparo de `PRICE_TARGET_REACHED`, documentado aqui.
    op.execute(
        """
        UPDATE mission_offer_relevance AS mor
        SET last_observation_id = latest.observation_id
        FROM (
            SELECT DISTINCT ON (cr.mission_id, po.offer_id)
                cr.mission_id AS mission_id,
                po.offer_id AS offer_id,
                po.id AS observation_id
            FROM price_observations AS po
            JOIN collection_runs AS cr ON cr.id = po.collection_run_id
            WHERE cr.mission_id IS NOT NULL
            ORDER BY cr.mission_id, po.offer_id, po.observed_at DESC, po.id DESC
        ) AS latest
        WHERE mor.mission_id = latest.mission_id
          AND mor.offer_id = latest.offer_id
          AND mor.last_observation_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_mission_offer_relevance_last_observation_id_price_observations"),
        "mission_offer_relevance",
        type_="foreignkey",
    )
    op.drop_column("mission_offer_relevance", "last_observation_id")
