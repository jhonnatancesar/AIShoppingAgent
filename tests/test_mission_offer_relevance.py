"""Testes da classificação de relevância (mission_id, offer_id), TASK-063."""

from app.collection.models import MissionOfferRelevance
from app.collection.relevance import OfferRelevance
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from sqlalchemy import ForeignKeyConstraint, Index


def test_mission_offer_relevance_uses_composite_identity() -> None:
    """Achado (auditoria TASK-112 fase 3B): `last_observation_id` foi
    adicionada na fase 3A (fonte de "previous" por Mission no fan-out
    compartilhado, DEC-048) e nunca refletida aqui -- defasagem já
    existente antes desta fase, não é regressão."""
    table = MissionOfferRelevance.__table__
    assert [column.name for column in table.columns] == [
        "mission_id",
        "offer_id",
        "classification",
        "classified_at",
        "last_observation_id",
        "created_at",
    ]
    assert tuple(column.name for column in table.primary_key.columns) == (
        "mission_id",
        "offer_id",
    )
    assert table.c.classification.nullable is False
    assert table.c.classified_at.type.timezone is True
    assert table.c.created_at.type.timezone is True


def test_mission_offer_relevance_references_history_with_restrict() -> None:
    foreign_keys = {
        tuple(constraint.columns)[0].name: tuple(constraint.elements)[0]
        for constraint in MissionOfferRelevance.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["mission_id"].target_fullname == "missions.id"
    assert foreign_keys["offer_id"].target_fullname == "offers.id"
    assert all(element.ondelete == "RESTRICT" for element in foreign_keys.values())


def test_mission_offer_relevance_has_reverse_lookup_and_registration() -> None:
    index = next(
        index
        for index in MissionOfferRelevance.__table__.indexes
        if isinstance(index, Index)
    )
    assert index.name == "ix_mission_offer_relevance_offer_id"
    assert tuple(column.name for column in index.columns) == ("offer_id",)
    assert MissionOfferRelevance in REGISTERED_MODELS
    assert (
        Base.metadata.tables["mission_offer_relevance"]
        is MissionOfferRelevance.__table__
    )


def test_offer_relevance_is_a_closed_vocabulary() -> None:
    assert {member.value for member in OfferRelevance} == {
        "match",
        "possible_match",
        "no_match",
    }
