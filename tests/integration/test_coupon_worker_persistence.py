"""Prova real (revisão 2026-09-10, segunda rodada) do UPSERT do Coupon
Worker (`coupons/persistence.py`, repositório separado
`AIShoppingAgentCupom-dev`) contra o PostgreSQL REAL do GG Oferta --
migrado de verdade (mesmo mecanismo de `scripts/run_integration_tests.py`
já usado por `test_coupons.py`), nunca SQLite.

Import cross-repositório deliberado: `PostgresCouponStore` é a classe
que roda de verdade em PROD (`worker.py` -> `open_coupon_store` ->
`COUPONS_POSTGRES_DSN` configurada) -- a suíte de integração do backend
GG (`test_coupons.py`) só EXERCITA leitura/consumo (`list_active_coupons`,
`best_applicable_coupon`), nunca o próprio UPSERT do worker. Reescrever
essa lógica aqui em SQL solto não provaria nada sobre o código real; por
isso importamos a classe real do outro repositório."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.integration

_COUPON_WORKER_REPO = Path(r"C:\App\AIShoppingAgentCupom-dev")
if str(_COUPON_WORKER_REPO) not in sys.path:
    sys.path.insert(0, str(_COUPON_WORKER_REPO))


def _dsn(engine) -> str:
    url = engine.url
    return (
        f"postgresql://{url.username}:{url.password}@{url.host}:{url.port}/{url.database}"
    )


def _open_store(integration_database):
    from coupons.persistence import PostgresCouponStore

    fd, sqlite_path = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    store = PostgresCouponStore(_dsn(integration_database.engine), sqlite_path)
    return store, sqlite_path


def _close_store(store, sqlite_path) -> None:
    import os
    store.close()
    os.unlink(sqlite_path)


def _real_coupon_rows(integration_database, *, code: str):
    from app.coupons.models import Coupon as GGCoupon
    from app.stores.models import Store

    with integration_database.sessions() as session:
        store_row = session.scalar(select(Store).where(Store.code == "mercadolivre"))
        rows = session.scalars(
            select(GGCoupon).where(
                GGCoupon.store_id == store_row.id, GGCoupon.code == code
            )
        ).all()
        return [
            {
                "discount_kind": r.discount_kind,
                "discount_value": r.discount_value,
                "scope_kind": r.scope_kind,
                "scope_reference": r.scope_reference,
                "minimum_purchase_amount": r.minimum_purchase_amount,
                "status": r.status,
                "evidence": r.evidence,
            }
            for r in rows
        ]


def test_postgres_upsert_enriches_same_row_never_duplicates(integration_database) -> None:
    """Mesma prova de `test_evidence.py` (SQLite), agora contra o
    PostgreSQL REAL do GG Oferta, usando a classe `PostgresCouponStore`
    de verdade (resolve `store_id` textual -> UUID real via `stores`,
    igual ao worker em PROD)."""
    from coupons.persistence import Coupon as WorkerCoupon

    store, sqlite_path = _open_store(integration_database)
    try:
        evidence = "mercadolivre:cards:PGTEST01:https://x.invalid/pg-produto-1"
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST01", discount_kind=None, discount_value=None,
            evidence=evidence, raw_rule_text="PGTEST01 (só código, 1a coleta)",
        ))
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST01", discount_kind="fixed_amount", discount_value=30.0,
            scope_kind="product", scope_reference="https://x.invalid/pg-produto-1",
            evidence=evidence, raw_rule_text="PGTEST01 -- R$30 OFF",
        ))
        rows = _real_coupon_rows(integration_database, code="PGTEST01")
        assert len(rows) == 1, "mesma evidence key -- enriquece a MESMA linha real no Postgres do GG, nunca duplica"
        assert rows[0]["discount_kind"] == "fixed_amount"
        assert float(rows[0]["discount_value"]) == 30.0
        assert rows[0]["scope_kind"] == "product"
        print("PASS (Postgres real): enriquecimento da mesma linha, sem duplicar")
    finally:
        _close_store(store, sqlite_path)


def test_postgres_upsert_never_erases_or_mixes_paired_fields(integration_database) -> None:
    """Prova real, no Postgres do GG: extração incompleta preserva o par
    completo antigo (discount_kind+discount_value); nunca mistura um
    campo novo com o par antigo de outro."""
    from coupons.persistence import Coupon as WorkerCoupon

    store, sqlite_path = _open_store(integration_database)
    try:
        evidence = "mercadolivre:cards:PGTEST02:https://x.invalid/pg-produto-2"
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST02", discount_kind="percentage", discount_value=12.0,
            scope_kind="product", scope_reference="https://x.invalid/pg-produto-2",
            evidence=evidence, raw_rule_text="PGTEST02 -- 12% OFF",
        ))
        # "Upsert malformado" -- só discount_kind vem, discount_value nulo.
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST02", discount_kind="fixed_amount", discount_value=None,
            scope_kind=None, scope_reference=None,
            evidence=evidence, raw_rule_text="PGTEST02 (extração parcial)",
        ))
        rows = _real_coupon_rows(integration_database, code="PGTEST02")
        assert len(rows) == 1
        assert rows[0]["discount_kind"] == "percentage", "nunca mistura discount_kind novo com discount_value antigo, mesmo no Postgres real"
        assert float(rows[0]["discount_value"]) == 12.0
        assert rows[0]["scope_kind"] == "product"
        assert rows[0]["scope_reference"] == "https://x.invalid/pg-produto-2"
        print("PASS (Postgres real): par discount_kind+discount_value nunca mistura novo com antigo")
    finally:
        _close_store(store, sqlite_path)


def test_postgres_upsert_creates_new_row_when_evidence_changes(integration_database) -> None:
    """Prova real, no Postgres do GG: mudar o texto da evidência (ex.:
    preço-base mudou, achado da Amazon) cria uma linha NOVA -- nunca
    sobrescreve a antiga."""
    from coupons.persistence import Coupon as WorkerCoupon

    store, sqlite_path = _open_store(integration_database)
    try:
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST03", discount_kind="fixed_amount", discount_value=10.0,
            evidence="mercadolivre:cards:PGTEST03:https://x.invalid/pg-produto-3-v1",
            raw_rule_text="PGTEST03 -- R$10 OFF (evidência 1)",
        ))
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST03", discount_kind="fixed_amount", discount_value=15.0,
            evidence="mercadolivre:cards:PGTEST03:https://x.invalid/pg-produto-3-v2",
            raw_rule_text="PGTEST03 -- R$15 OFF (evidência 2, preço-base mudou)",
        ))
        rows = _real_coupon_rows(integration_database, code="PGTEST03")
        assert len(rows) == 2, "evidence key diferente -- linha NOVA real no Postgres, nunca sobrescreve"
        values = sorted(float(r["discount_value"]) for r in rows)
        assert values == [10.0, 15.0]
        print("PASS (Postgres real): evidence key diferente cria linha nova, nunca funde")
    finally:
        _close_store(store, sqlite_path)


def test_postgres_expire_stale_marks_only_unconfirmed_active_rows(integration_database) -> None:
    """Prova real, no Postgres do GG: `expire_stale` (chamado pelo worker
    só após rodada `status=='ok'`, `coupons/scanner.py`) marca como
    `expired` só o que não foi reconfirmado, usando `PostgresCouponStore.
    expire_stale` de verdade (resolve UUID, compara `last_seen_at` real
    no Postgres)."""
    from datetime import UTC, datetime

    from coupons.persistence import Coupon as WorkerCoupon

    store, sqlite_path = _open_store(integration_database)
    try:
        evidence = "mercadolivre:cards:PGTEST04:https://x.invalid/pg-produto-4"
        store.upsert(WorkerCoupon(
            store_id="mercadolivre", code="PGTEST04", discount_kind="fixed_amount", discount_value=8.0,
            evidence=evidence, raw_rule_text="PGTEST04 -- R$8 OFF", status="active",
        ))
        cutoff = datetime.now(UTC).isoformat()
        expired = store.expire_stale("mercadolivre", cutoff)
        assert expired >= 1
        rows = _real_coupon_rows(integration_database, code="PGTEST04")
        assert rows[0]["status"] == "expired"
        print("PASS (Postgres real): expire_stale marca ausência real usando a classe do worker contra o Postgres do GG")
    finally:
        _close_store(store, sqlite_path)
