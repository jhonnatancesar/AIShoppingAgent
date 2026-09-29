"""TASK-132 (Parte A) -- varredura de títulos novos, sem banco: reserva e
resolução são injetadas; o efeito real fica na suíte de integração."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.collection.orchestration import CollectionOrchestrator
from app.products import listing_title_sweep as sweep
from app.products.listing_title_sweep import _Claim, sweep_listing_titles


def _run(budget: int = 3):
    return asyncio.run(
        sweep_listing_titles(MagicFactory(), ai_manager=object(), budget=budget)
    )


class MagicFactory:
    pass


def test_zero_budget_never_claims(monkeypatch) -> None:
    claim = AsyncMock()
    monkeypatch.setattr(sweep, "_claim", claim)

    assert _run(budget=0).claimed == 0
    claim.assert_not_awaited()


def test_outcomes_are_counted_and_failures_never_stop_the_others(monkeypatch) -> None:
    claims = [_Claim(uuid4(), f"titulo {i}") for i in range(4)]
    monkeypatch.setattr(sweep, "_claim", AsyncMock(return_value=claims))
    outcomes = ["changed", "resolved_same", RuntimeError("boom"), "unresolved"]
    monkeypatch.setattr(sweep, "_resolve_one", AsyncMock(side_effect=outcomes))

    summary = _run()

    assert (summary.claimed, summary.changed, summary.resolved_same) == (4, 1, 1)
    assert summary.unresolved == 2


def _orchestrator(budget: int, learning: bool = True):
    orchestrator = object.__new__(CollectionOrchestrator)
    orchestrator._settings = SimpleNamespace(
        product_identity_learning_enabled=learning, listing_title_check_budget=budget
    )
    orchestrator._session_factory = object()
    orchestrator._ai_manager = object()
    orchestrator._ai_profile = None
    orchestrator._arbiter_ai_manager = None
    return orchestrator


def test_orchestrator_sweep_respects_flag_and_budget(monkeypatch) -> None:
    called = AsyncMock(return_value=sweep.ListingTitleSweepSummary(claimed=1))
    monkeypatch.setattr("app.collection.orchestration.sweep_listing_titles", called)

    asyncio.run(_orchestrator(0)._sweep_listing_titles())
    asyncio.run(_orchestrator(3, learning=False)._sweep_listing_titles())
    called.assert_not_awaited()

    asyncio.run(_orchestrator(3)._sweep_listing_titles())
    called.assert_awaited_once()


def test_orchestrator_sweep_failure_never_breaks_the_cycle(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.collection.orchestration.sweep_listing_titles",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    asyncio.run(_orchestrator(3)._sweep_listing_titles())
