from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest
from app.authentication.models import CredentialAction
from app.collection.contracts import MarketplacePartyKind, OfferCondition
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.events import (
    EVENT_CATALOG,
    AggregateType,
    AppliedCouponPayload,
    AuthenticationCompletedPayload,
    AuthenticationSessionPayload,
    AvailabilityChangedPayload,
    CollectionCompletedPayload,
    CollectionFailedPayload,
    EventCatalogError,
    EventType,
    MissionPrelistErrataPayload,
    MissionPrelistErrataV2Payload,
    MissionPrelistReadyPayload,
    MissionPrelistReadyV2Payload,
    MissionStatusChangedPayload,
    MissionVariantsReadyPayload,
    PrelistOfferPayload,
    PriceDecreasedPayload,
    PriceTargetReachedPayload,
    ProductVariantOptionPayload,
    resolve_event_spec,
    validate_event_payload,
)
from app.events.catalog import _validate_money, _validate_prelist_offers
from app.missions.models import MissionStatus


def test_catalog_is_closed_versioned_and_has_expected_aggregates() -> None:
    assert isinstance(EVENT_CATALOG, MappingProxyType)
    assert set(EVENT_CATALOG) == set(EventType)
    assert all(event_type.value.endswith((".v1", ".v2")) for event_type in EventType)
    assert (
        resolve_event_spec("price.target_reached.v1").aggregate_type
        is AggregateType.MISSION
    )
    assert (
        resolve_event_spec(EventType.PRICE_DECREASED_V1).aggregate_type
        is AggregateType.OFFER
    )


def test_catalog_accepts_exact_payload_contract() -> None:
    payload = CollectionCompletedPayload(uuid4(), uuid4(), uuid4(), 3)

    spec = validate_event_payload(EventType.COLLECTION_COMPLETED_V1, payload)

    assert spec.payload_type is CollectionCompletedPayload


def test_authentication_contracts_are_closed_and_require_aware_expiry() -> None:
    user_id = uuid4()
    completed = AuthenticationCompletedPayload(user_id, CredentialAction.LOGIN)
    assert (
        validate_event_payload(
            EventType.AUTHENTICATION_COMPLETED_V1, completed
        ).aggregate_type
        is AggregateType.USER
    )
    with pytest.raises(EventCatalogError, match="CredentialAction"):
        AuthenticationCompletedPayload(user_id, "login")  # type: ignore[arg-type]
    with pytest.raises(EventCatalogError, match="timezone-aware"):
        AuthenticationSessionPayload(uuid4(), user_id, datetime(2026, 8, 9, 12))
    payload = AuthenticationSessionPayload(
        uuid4(), user_id, datetime(2026, 8, 9, 12, tzinfo=UTC)
    )
    assert (
        validate_event_payload(
            EventType.AUTHENTICATION_SESSION_EXPIRING_V1, payload
        ).aggregate_type
        is AggregateType.AUTH_SESSION
    )


def test_catalog_rejects_unknown_event_and_wrong_payload() -> None:
    payload = CollectionCompletedPayload(uuid4(), uuid4(), None, 0)
    with pytest.raises(EventCatalogError, match="unknown event type"):
        resolve_event_spec("price.unversioned")
    with pytest.raises(EventCatalogError, match="requires PriceDecreasedPayload"):
        validate_event_payload(EventType.PRICE_DECREASED_V1, payload)


def test_mission_transition_contract_requires_a_real_transition() -> None:
    with pytest.raises(EventCatalogError, match="status must change"):
        MissionStatusChangedPayload(
            uuid4(), uuid4(), MissionStatus.ACTIVE, MissionStatus.ACTIVE, 1
        )
    with pytest.raises(EventCatalogError, match="state_version"):
        MissionStatusChangedPayload(
            uuid4(), uuid4(), MissionStatus.DRAFT, MissionStatus.ACTIVE, 0
        )


def test_collection_contract_rejects_negative_count() -> None:
    with pytest.raises(EventCatalogError, match="observation_count"):
        CollectionCompletedPayload(uuid4(), uuid4(), None, -1)


def test_collection_failure_requires_stable_sanitized_code() -> None:
    with pytest.raises(EventCatalogError, match="snake_case"):
        CollectionFailedPayload(uuid4(), uuid4(), None, "Timeout contacting https://x")


def test_price_decrease_contract_uses_exact_decreasing_money() -> None:
    payload = PriceDecreasedPayload(
        uuid4(), uuid4(), uuid4(), Decimal("100.0000"), Decimal("90.0000"), "BRL"
    )
    assert payload.current_total == Decimal("90.0000")

    with pytest.raises(EventCatalogError, match="lower"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.0000"),
            Decimal("100.0000"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="Decimal"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100"),
            90.0,
            "BRL",  # type: ignore[arg-type]
        )


def test_coupon_snapshot_is_optional_and_backward_compatible() -> None:
    """Correção 2026-09-06: campo opcional, retrocompatível -- eventos
    sem cupom continuam válidos exatamente como antes."""
    payload = PriceDecreasedPayload(
        uuid4(), uuid4(), uuid4(), Decimal("100.00"), Decimal("90.00"), "BRL"
    )
    assert payload.coupon is None


def test_coupon_snapshot_final_amount_must_equal_current_total() -> None:
    """O cupom e `current_total` precisam representar a MESMA
    oportunidade -- catálogo recusa qualquer divergência."""
    coupon = AppliedCouponPayload(
        coupon_id=uuid4(),
        code="PROMO",
        discount_kind="fixed_amount",
        original_amount=Decimal("100.00"),
        discount_amount=Decimal("10.00"),
        final_amount=Decimal("90.00"),
        currency="BRL",
    )
    payload = PriceDecreasedPayload(
        uuid4(), uuid4(), uuid4(), Decimal("100.00"), Decimal("90.00"), "BRL", coupon
    )
    assert payload.coupon is coupon

    with pytest.raises(EventCatalogError, match="same opportunity"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.00"),
            Decimal("85.00"),  # não bate com coupon.final_amount (90.00)
            "BRL",
            coupon,
        )


def test_coupon_snapshot_currency_must_match_alert_currency() -> None:
    coupon = AppliedCouponPayload(
        coupon_id=uuid4(),
        code="PROMO",
        discount_kind="fixed_amount",
        original_amount=Decimal("100.00"),
        discount_amount=Decimal("10.00"),
        final_amount=Decimal("90.00"),
        currency="USD",
    )
    with pytest.raises(EventCatalogError, match="currency"):
        PriceTargetReachedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.00"),
            Decimal("90.00"),
            "BRL",
            coupon,
        )


def test_coupon_snapshot_discount_must_not_exceed_original_amount() -> None:
    with pytest.raises(EventCatalogError, match="discount_amount"):
        AppliedCouponPayload(
            coupon_id=uuid4(),
            code="PROMO",
            discount_kind="fixed_amount",
            original_amount=Decimal("50.00"),
            discount_amount=Decimal("60.00"),
            final_amount=Decimal("0.00"),
            currency="BRL",
        )


def test_coupon_snapshot_final_amount_must_equal_original_minus_discount() -> None:
    with pytest.raises(EventCatalogError, match="final_amount"):
        AppliedCouponPayload(
            coupon_id=uuid4(),
            code="PROMO",
            discount_kind="fixed_amount",
            original_amount=Decimal("100.00"),
            discount_amount=Decimal("10.00"),
            final_amount=Decimal("50.00"),
            currency="BRL",
        )


def test_target_and_availability_contracts_enforce_their_facts() -> None:
    with pytest.raises(EventCatalogError, match="target_total"):
        PriceTargetReachedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.0000"),
            Decimal("100.0001"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="availability must change"):
        AvailabilityChangedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Availability.AVAILABLE,
            Availability.AVAILABLE,
        )


@pytest.mark.parametrize("currency", ["brl", "BR", "BR1", "ÉUR"])
def test_money_contract_rejects_invalid_currency(currency: str) -> None:
    with pytest.raises(EventCatalogError, match="currency"):
        PriceTargetReachedPayload(
            uuid4(), uuid4(), uuid4(), Decimal("100"), Decimal("90"), currency
        )


def test_prelist_ready_contract_allows_one_or_two_offers_lowest_first() -> None:
    mission_id, first_offer, first_obs = uuid4(), uuid4(), uuid4()

    single = MissionPrelistReadyPayload(
        mission_id, first_offer, first_obs, Decimal("100.00"), "BRL"
    )
    assert single.second_offer_id is None

    second_offer, second_obs = uuid4(), uuid4()
    pair = MissionPrelistReadyPayload(
        mission_id,
        first_offer,
        first_obs,
        Decimal("100.00"),
        "BRL",
        second_offer,
        second_obs,
        Decimal("150.00"),
        "BRL",
    )
    assert pair.second_amount == Decimal("150.00")

    with pytest.raises(EventCatalogError, match="lowest"):
        MissionPrelistReadyPayload(
            mission_id,
            first_offer,
            first_obs,
            Decimal("150.00"),
            "BRL",
            second_offer,
            second_obs,
            Decimal("100.00"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="differ"):
        MissionPrelistReadyPayload(
            mission_id,
            first_offer,
            first_obs,
            Decimal("100.00"),
            "BRL",
            first_offer,
            second_obs,
            Decimal("150.00"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="complete or all absent"):
        MissionPrelistReadyPayload(
            mission_id,
            first_offer,
            first_obs,
            Decimal("100.00"),
            "BRL",
            second_offer_id=second_offer,
        )


def test_prelist_errata_contract_requires_strictly_cheaper() -> None:
    mission_id, offer_id, observation_id = uuid4(), uuid4(), uuid4()

    first_ever = MissionPrelistErrataPayload(
        mission_id, offer_id, observation_id, Decimal("100.00"), "BRL", None
    )
    assert first_ever.previous_lowest_amount is None

    correction = MissionPrelistErrataPayload(
        mission_id, offer_id, observation_id, Decimal("80.00"), "BRL", Decimal("100.00")
    )
    assert correction.current_amount == Decimal("80.00")

    with pytest.raises(EventCatalogError, match="lower"):
        MissionPrelistErrataPayload(
            mission_id,
            offer_id,
            observation_id,
            Decimal("100.00"),
            "BRL",
            Decimal("100.00"),
        )


def test_prelist_v2_contract_carries_ordered_offer_collection() -> None:
    mission_id, store_id = uuid4(), uuid4()
    offers = tuple(
        PrelistOfferPayload(
            offer_id=uuid4(),
            observation_id=uuid4(),
            store_id=store_id,
            amount=Decimal(str(100 + index)),
            total_amount=Decimal(str(100 + index)),
            currency="BRL",
            relevance=(
                OfferRelevance.MATCH if index < 4 else OfferRelevance.POSSIBLE_MATCH
            ),
            condition=OfferCondition.NEW,
            seller_kind=MarketplacePartyKind.PLATFORM,
            availability=Availability.AVAILABLE,
        )
        for index in range(5)
    )

    ready = MissionPrelistReadyV2Payload(mission_id, offers)
    errata = MissionPrelistErrataV2Payload(mission_id, uuid4(), store_id, offers)

    assert ready.offers == offers
    assert errata.offers == offers
    assert resolve_event_spec(EventType.MISSION_PRELIST_READY_V2).payload_type is (
        MissionPrelistReadyV2Payload
    )


def test_mission_transition_contract_rejects_non_mission_status_types() -> None:
    with pytest.raises(EventCatalogError, match="MissionStatus"):
        MissionStatusChangedPayload(
            uuid4(),
            uuid4(),
            "draft",
            MissionStatus.ACTIVE,
            1,  # type: ignore[arg-type]
        )


def test_coupon_payload_rejects_non_string_code() -> None:
    with pytest.raises(EventCatalogError, match="code must be a string"):
        AppliedCouponPayload(
            coupon_id=uuid4(),
            code=123,  # type: ignore[arg-type]
            discount_kind="fixed_amount",
            original_amount=Decimal("100.00"),
            discount_amount=Decimal("10.00"),
            final_amount=Decimal("90.00"),
            currency="BRL",
        )


def test_coupon_snapshot_rejects_non_applied_coupon_type() -> None:
    with pytest.raises(EventCatalogError, match="AppliedCouponPayload"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.00"),
            Decimal("90.00"),
            "BRL",
            {"final_amount": Decimal("90.00")},  # type: ignore[arg-type]
        )


def test_availability_contract_rejects_non_availability_types() -> None:
    with pytest.raises(EventCatalogError, match="must use Availability"):
        AvailabilityChangedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            "available",  # type: ignore[arg-type]
            Availability.UNAVAILABLE,
        )


def _valid_prelist_offer(**overrides: object) -> PrelistOfferPayload:
    fields: dict[str, object] = {
        "offer_id": uuid4(),
        "observation_id": uuid4(),
        "store_id": uuid4(),
        "amount": Decimal("100.00"),
        "total_amount": Decimal("100.00"),
        "currency": "BRL",
        "relevance": OfferRelevance.MATCH,
        "condition": OfferCondition.NEW,
        "seller_kind": MarketplacePartyKind.PLATFORM,
        "availability": Availability.AVAILABLE,
    }
    fields.update(overrides)
    return PrelistOfferPayload(**fields)


def test_prelist_offer_payload_enforces_all_its_own_invariants() -> None:
    with pytest.raises(EventCatalogError, match="total_amount"):
        _valid_prelist_offer(total_amount=Decimal("99.00"))
    with pytest.raises(EventCatalogError, match="must be relevant"):
        _valid_prelist_offer(relevance=OfferRelevance.NO_MATCH)
    with pytest.raises(EventCatalogError, match="OfferCondition"):
        _valid_prelist_offer(condition="new")  # type: ignore[arg-type]
    with pytest.raises(EventCatalogError, match="MarketplacePartyKind"):
        _valid_prelist_offer(seller_kind="platform")  # type: ignore[arg-type]
    with pytest.raises(EventCatalogError, match="availability must use Availability"):
        _valid_prelist_offer(availability="available")  # type: ignore[arg-type]


def test_validate_prelist_offers_rejects_out_of_range_offer_count() -> None:
    with pytest.raises(EventCatalogError, match="between 1 and 20"):
        _validate_prelist_offers(())


def test_validate_prelist_offers_rejects_non_prelist_offer_items() -> None:
    with pytest.raises(EventCatalogError, match="PrelistOfferPayload"):
        _validate_prelist_offers((_valid_prelist_offer(), "not a payload"))  # type: ignore[arg-type]


def test_validate_prelist_offers_rejects_duplicate_offer_ids() -> None:
    duplicate = _valid_prelist_offer()
    with pytest.raises(EventCatalogError, match="must be unique"):
        _validate_prelist_offers((duplicate, duplicate))


def test_validate_prelist_offers_rejects_more_than_five_per_store() -> None:
    store_id = uuid4()
    offers = tuple(_valid_prelist_offer(store_id=store_id) for _ in range(6))
    with pytest.raises(EventCatalogError, match="at most five offers per store"):
        _validate_prelist_offers(offers)


def test_validate_prelist_offers_errata_rejects_offer_outside_corrected_store() -> None:
    with pytest.raises(EventCatalogError, match="corrected_store_id"):
        _validate_prelist_offers((_valid_prelist_offer(),), one_store=uuid4())


def test_product_variant_option_rejects_blank_or_non_string_label() -> None:
    with pytest.raises(EventCatalogError, match="must not be blank"):
        ProductVariantOptionPayload(uuid4(), "   ")
    with pytest.raises(EventCatalogError, match="must not be blank"):
        ProductVariantOptionPayload(uuid4(), None)  # type: ignore[arg-type]


def test_mission_variants_ready_payload_enforces_all_its_own_invariants() -> None:
    mission_id = uuid4()
    variant = ProductVariantOptionPayload(uuid4(), "16GB/512GB")

    with pytest.raises(EventCatalogError, match="between 1 and 20"):
        MissionVariantsReadyPayload(mission_id, (), 1)
    with pytest.raises(EventCatalogError, match="ProductVariantOptionPayload"):
        MissionVariantsReadyPayload(
            mission_id,
            (variant, "not a variant"),
            1,  # type: ignore[arg-type]
        )
    with pytest.raises(EventCatalogError, match="must be unique"):
        duplicate_variant = ProductVariantOptionPayload(variant.product_id, "8GB/256GB")
        MissionVariantsReadyPayload(mission_id, (variant, duplicate_variant), 1)
    with pytest.raises(EventCatalogError, match="state_version"):
        MissionVariantsReadyPayload(mission_id, (variant,), -1)

    ready = MissionVariantsReadyPayload(mission_id, (variant,), 1)
    assert ready.variants == (variant,)


def test_validate_money_rejects_negative_amount() -> None:
    with pytest.raises(EventCatalogError, match="non-negative"):
        _validate_money(Decimal("-1.00"), "BRL")
