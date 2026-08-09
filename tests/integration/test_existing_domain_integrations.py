"""Validações permanentes dos serviços que exigem PostgreSQL real."""

import pytest

from backend.scripts.validate_authorization import validate as validate_authorization
from backend.scripts.validate_limits_resilience import main as validate_resilience
from backend.scripts.validate_password_authentication import (
    main as validate_authentication,
)
from backend.scripts.validate_privacy import main as validate_privacy
from backend.scripts.validate_purchase_trail import validate as validate_purchase_trail
from backend.scripts.validate_recommendation_flow import (
    validate as validate_recommendation_flow,
)

pytestmark = pytest.mark.integration


def test_recommendation_comparison_and_confirmation(integration_database) -> None:
    validate_recommendation_flow()


def test_purchase_trail_and_concurrent_resolution(integration_database) -> None:
    validate_purchase_trail()


def test_password_authentication_and_single_use_concurrency(
    integration_database,
) -> None:
    validate_authentication()


def test_authorization_hierarchy_and_ownership(integration_database) -> None:
    validate_authorization()


def test_persistent_replay_rate_limit_and_event_resilience(
    integration_database,
) -> None:
    validate_resilience()


def test_privacy_cleanup_and_fail_closed_deidentification(
    integration_database,
) -> None:
    validate_privacy()
