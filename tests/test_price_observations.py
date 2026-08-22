from app.collection.models import PriceObservation
from app.database.model_registry import REGISTERED_MODELS


def test_price_observation_is_append_only_schema() -> None:
    table = PriceObservation.__table__
    assert PriceObservation in REGISTERED_MODELS
    assert "updated_at" not in table.c
    assert table.c.offer_id.nullable is False
    assert table.c.collection_run_id.nullable is False
    assert table.c.amount.type.precision == 19 and table.c.amount.type.scale == 4
    assert table.c.shipping_amount.nullable is True
    assert table.c.seller_kind.nullable is True
    assert table.c.fulfillment_kind.nullable is True
    assert table.c.condition.nullable is False
    assert {c.name for c in table.constraints} >= {
        "ck_price_observations_total_exact",
        "ck_price_observations_currency_iso4217",
        "ck_price_observations_amounts_non_negative",
        "ck_price_observations_seller_kind_values",
        "ck_price_observations_fulfillment_kind_values",
        "ck_price_observations_condition_values",
    }
