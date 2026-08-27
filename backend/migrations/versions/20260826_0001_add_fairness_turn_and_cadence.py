"""Add fairness turn audit trail, monitoring item due index, cadence policy tables, and legacy per-source cadence (TASK-112 fase 3B).

Revision ID: 20260826_0001
Revises: 20260825_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0001"
down_revision: str | None = "20260825_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `last_fairness_turn_id`: rastro de auditoria/depuração de qual
    # ciclo (`run_batch`) creditou o último avanço deste usuário -- NÃO é
    # o mecanismo de exclusão mútua entre execuções concorrentes (isso é
    # o lock de linha contínuo adquirido por `app.collection.fairness.
    # _reserve_fairness_owners`).
    op.add_column(
        "user_collection_queue_state",
        sa.Column("last_fairness_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # `fairness_owner_user_id`: usuário que consumiu o turno de fairness
    # de uma CollectionRun compartilhada -- trilha de auditoria completa
    # (CollectionRun -> monitoring_item_id -> store_id ->
    # fairness_owner_user_id) numa linha só. `ondelete=RESTRICT`:
    # `app.privacy.service.deidentify_account` nunca apaga a linha
    # `User`, só remove credenciais/`telegram_user_id` -- a FK nunca fica
    # pendurada na prática.
    op.add_column(
        "collection_runs",
        sa.Column("fairness_owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_collection_runs_fairness_owner_user_id_users"),
        "collection_runs",
        "users",
        ["fairness_owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_collection_runs_fairness_owner_requires_shared",
        "collection_runs",
        "fairness_owner_user_id IS NULL OR monitoring_item_id IS NOT NULL",
    )

    # Índice de seleção (`_select_due_work_for_batch`) -- sem ele, a
    # query de candidatos compartilhados faria sequential scan completo
    # de `monitoring_item_stores` a cada ciclo do worker. Espelha `ix_
    # mission_schedules_due`, que já existe para o mesmo fim em
    # `mission_schedules`.
    op.create_index(
        "ix_monitoring_item_stores_due",
        "monitoring_item_stores",
        ["next_run_at", "monitoring_item_id", "store_id"],
        postgresql_where=sa.text("is_enabled"),
    )

    # Calendário promocional determinístico -- dado, não código; ADMIN
    # insere/remove linhas sem NENHUMA migration nova para adicionar uma
    # nova janela (Black Friday, datas duplas, etc.).
    op.create_table(
        "promotional_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_promotional_windows_time_order")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotional_windows")),
    )

    # Estado mínimo por loja (histerese de HIGH_ACTIVITY) -- a contagem
    # de mudanças em si nunca é cacheada aqui, sempre recomputada ao vivo
    # a partir de `price_observations` (ver `app.collection.cadence`).
    op.create_table(
        "store_activity_state",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("high_activity_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_store_activity_state_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("store_id", name=op.f("pk_store_activity_state")),
    )

    # Correção estrutural (achado da auditoria pós-3B): a unidade real de
    # execução do caminho legado é `(Mission, Store)` -- `MissionSource`
    # --, não `Mission` inteira via `MissionSchedule`. `MissionSchedule`
    # (agenda por MISSÃO) nunca soube que Amazon podia estar em
    # HIGH_ACTIVITY (30-45min) e KaBuM em NORMAL (45-75min) na mesma
    # Mission -- a versão anterior desta fase tomava a decisão mais cedo
    # entre as lojas e reagendava a Mission inteira nesse horário,
    # fazendo KaBuM ser recoletada antes da sua própria cadência só
    # porque a Mission "acordou" para atender Amazon. `next_run_at`/
    # `last_run_at` por `(mission_id, store_id)` espelham exatamente
    # `MonitoringItemStore` no caminho compartilhado -- mesma política de
    # cadência (`app.collection.cadence`), agora aplicada por STORE em
    # vez de por MISSÃO nos dois caminhos. NULL em `next_run_at` = ainda
    # não coletada, elegível imediatamente (dado antigo sem agenda
    # individual nunca fica bloqueado por um valor "no escuro").
    # `MissionSchedule` permanece na tabela como agregado DERIVADO
    # (`MIN`/`MAX` sobre as `MissionSource` da missão) só para exibição
    # (API de detalhe da missão, dashboard ADMIN) e como o kill-switch
    # independente (`is_enabled`) que ADMIN/privacidade já usavam --
    # nunca mais lido por nenhuma decisão real de claim.
    op.add_column(
        "mission_sources",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mission_sources",
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Sem índice dedicado aqui (ao contrário de `ix_monitoring_item_
    # stores_due` acima) -- `EXPLAIN ANALYZE` contra 5000 `MissionSource`
    # sintéticas (2% due) mostrou o planejador preferindo ancorar a busca
    # pela tabela `missions` (pequena, já filtrada por `status='active'`)
    # e alcançar `mission_sources` via os índices que já existem
    # (`pk_mission_sources` por `mission_id`, `uq_mission_schedules_
    # mission_id`) -- zero sequential scan, ~16ms mesmo nesse volume bem
    # acima da escala real da V1.2 (ver `tests/integration/test_mission_
    # source_index_explain.py`). Um índice não usado pelo planejador só
    # custaria escrita extra a cada claim (`next_run_at` muda em toda
    # execução bem-sucedida) sem benefício de leitura -- não projetado
    # "no escuro" na direção oposta também.

    # `MissionSchedule` vira agregado DERIVADO (MIN/MAX sobre as
    # `MissionSource` da missão, ver `_refresh_legacy_schedule_aggregate`)
    # -- `last_run_at <= next_run_at` deixa de ser um invariante válido:
    # as duas colunas podem vir de sources DIFERENTES, claimadas em
    # momentos diferentes com cadências diferentes (a própria correção
    # estrutural desta migration -- uma store HIGH_ACTIVITY recém-
    # claimada pode ter `last_run_at` mais recente que o `next_run_at`
    # (mais cedo) de OUTRA store da mesma missão ainda não due). O
    # invariante em si (uma execução não pode ter acontecido DEPOIS do
    # seu próprio próximo agendamento) continua válido por CONSTRUÇÃO
    # dentro de cada `MissionSource` individual -- só não faz mais
    # sentido como comparação cruzada no agregado.
    op.drop_constraint(
        "ck_mission_schedules_run_order", "mission_schedules", type_="check"
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_mission_schedules_run_order",
        "mission_schedules",
        "last_run_at IS NULL OR last_run_at <= next_run_at",
    )
    op.drop_column("mission_sources", "last_run_at")
    op.drop_column("mission_sources", "next_run_at")
    op.drop_table("store_activity_state")
    op.drop_table("promotional_windows")
    op.drop_index("ix_monitoring_item_stores_due", table_name="monitoring_item_stores")
    op.drop_constraint(
        "ck_collection_runs_fairness_owner_requires_shared",
        "collection_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_collection_runs_fairness_owner_user_id_users"),
        "collection_runs",
        type_="foreignkey",
    )
    op.drop_column("collection_runs", "fairness_owner_user_id")
    op.drop_column("user_collection_queue_state", "last_fairness_turn_id")
