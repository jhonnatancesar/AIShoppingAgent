from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.audit.models import AuditEntry
from app.authorization import (
    ROLE_PERMISSIONS,
    AuthorizationDenialReason,
    AuthorizationDenied,
    Permission,
    ai_profile_for_user,
    authorize,
    deny_resource_unavailable,
    permissions_for_role,
)
from app.users.models import UserRole


def _user(role: object = UserRole.USER) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role=role)


def test_role_permissions_form_the_approved_strict_hierarchy() -> None:
    user = ROLE_PERMISSIONS[UserRole.USER]
    admin = ROLE_PERMISSIONS[UserRole.ADMIN]
    dev = ROLE_PERMISSIONS[UserRole.DEV]

    assert user < admin < dev
    assert Permission.AI_PROFILE_ADMIN not in user
    assert Permission.AI_PROFILE_DEV not in admin
    assert Permission.AI_PROFILE_DEV in dev


@pytest.mark.parametrize("role", [None, "ADMIN", "DEV", "OWNER", object()])
def test_missing_or_unknown_role_fails_closed_and_is_audited(role: object) -> None:
    session = MagicMock()
    user = _user(role)

    with pytest.raises(AuthorizationDenied) as raised:
        authorize(session, user, Permission.MISSION_READ)

    expected = (
        AuthorizationDenialReason.MISSING_ROLE
        if role is None
        else AuthorizationDenialReason.UNKNOWN_ROLE
    )
    assert raised.value.reason is expected
    assert permissions_for_role(role) == frozenset()
    audit = session.add.call_args.args[0]
    assert isinstance(audit, AuditEntry)
    assert audit.action == "authorization.denied"
    assert audit.actor_id == user.id
    assert audit.resource_id == user.id
    assert audit.entry_metadata == {
        "permission": "mission.read",
        "reason": expected.value,
        "role": "unknown",
    }


def test_permission_denial_records_only_controlled_metadata() -> None:
    session = MagicMock()
    user = _user(UserRole.USER)
    resource_id = uuid4()

    with pytest.raises(AuthorizationDenied) as raised:
        authorize(
            session,
            user,
            Permission.AI_PROFILE_ADMIN,
            resource_type="mission",
            resource_id=resource_id,
        )

    assert raised.value.reason is AuthorizationDenialReason.PERMISSION_DENIED
    audit = session.add.call_args.args[0]
    assert audit.resource_type == "mission"
    assert audit.resource_id == resource_id
    assert set(audit.entry_metadata) == {"permission", "reason", "role"}


def test_resource_denial_does_not_reveal_whether_resource_exists() -> None:
    session = MagicMock()
    user = _user(UserRole.DEV)
    resource_id = uuid4()

    with pytest.raises(AuthorizationDenied) as raised:
        deny_resource_unavailable(
            session,
            user,
            Permission.MISSION_READ,
            resource_type="mission",
            resource_id=resource_id,
        )

    assert raised.value.reason is AuthorizationDenialReason.RESOURCE_UNAVAILABLE
    assert str(raised.value) == "authorization denied"


@pytest.mark.parametrize("role", list(UserRole))
def test_ai_profile_is_derived_only_from_persisted_role(role: UserRole) -> None:
    session = MagicMock()
    user = _user(role)

    assert ai_profile_for_user(session, user) is role
    session.add.assert_not_called()
