"""Autorização central da aplicação."""

from app.authorization.policy import (
    ROLE_PERMISSIONS,
    AuthorizationDenialReason,
    AuthorizationDenied,
    Permission,
    ai_profile_for_user,
    authorize,
    deny_resource_unavailable,
    permissions_for_role,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "AuthorizationDenied",
    "AuthorizationDenialReason",
    "Permission",
    "ai_profile_for_user",
    "authorize",
    "deny_resource_unavailable",
    "permissions_for_role",
]
