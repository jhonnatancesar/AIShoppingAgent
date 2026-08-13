"""Política RBAC fail-closed da V1, separada da autenticação do canal.

Aceita `Session` ou `AsyncSession` (extensão da TASK-079): estas funções só
chamam `session.add`, que não faz I/O nem precisa de `await` em nenhum dos
dois tipos -- por isso não há (nem é necessária) uma versão `_async`
separada."""

from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.audit.models import AuditEntry
from app.users.models import User, UserRole


class Permission(StrEnum):
    """Capacidades concretas existentes na V1."""

    TELEGRAM_INTERACT = "telegram.interact"
    PROFILE_MANAGE = "profile.manage"
    NOTIFICATION_PREFERENCES_MANAGE = "notification_preferences.manage"
    MISSION_CREATE = "mission.create"
    MISSION_READ = "mission.read"
    MISSION_TRANSITION = "mission.transition"
    MISSION_EDIT = "mission.edit"
    PURCHASE_RECOMMEND = "purchase.recommend"
    PURCHASE_COMPARE = "purchase.compare"
    PURCHASE_CONFIRM = "purchase.confirm"
    PURCHASE_TRAIL_READ = "purchase_trail.read"
    AI_INTERPRET = "ai.interpret"
    AI_PROFILE_USER = "ai_profile.user"
    AI_PROFILE_ADMIN = "ai_profile.admin"
    AI_PROFILE_DEV = "ai_profile.dev"


class AuthorizationDenialReason(StrEnum):
    """Motivos fechados que podem aparecer em auditoria e logs."""

    MISSING_ROLE = "missing_role"
    UNKNOWN_ROLE = "unknown_role"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


class AuthorizationDenied(PermissionError):
    """Uma identidade autenticada não pode executar a capacidade solicitada."""

    def __init__(
        self,
        permission: Permission,
        reason: AuthorizationDenialReason,
    ) -> None:
        super().__init__("authorization denied")
        self.permission = permission
        self.reason = reason


_USER_PERMISSIONS = frozenset(
    {
        Permission.TELEGRAM_INTERACT,
        Permission.PROFILE_MANAGE,
        Permission.NOTIFICATION_PREFERENCES_MANAGE,
        Permission.MISSION_CREATE,
        Permission.MISSION_READ,
        Permission.MISSION_TRANSITION,
        Permission.MISSION_EDIT,
        Permission.PURCHASE_RECOMMEND,
        Permission.PURCHASE_COMPARE,
        Permission.PURCHASE_CONFIRM,
        Permission.PURCHASE_TRAIL_READ,
        Permission.AI_INTERPRET,
        Permission.AI_PROFILE_USER,
    }
)
_ADMIN_PERMISSIONS = _USER_PERMISSIONS | {Permission.AI_PROFILE_ADMIN}
_DEV_PERMISSIONS = _ADMIN_PERMISSIONS | {Permission.AI_PROFILE_DEV}

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.USER: _USER_PERMISSIONS,
    UserRole.ADMIN: _ADMIN_PERMISSIONS,
    UserRole.DEV: _DEV_PERMISSIONS,
}


def permissions_for_role(role: object) -> frozenset[Permission]:
    """Devolve a política exata; papel ausente/desconhecido nunca ganha acesso."""
    if not isinstance(role, UserRole):
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset())


def authorize(
    session: Session | AsyncSession,
    user: User,
    permission: Permission,
    *,
    resource_type: str = "user",
    resource_id: UUID | None = None,
) -> None:
    """Exige uma permissão e audita somente o caminho de recusa."""
    role = getattr(user, "role", None)
    if role is None:
        _deny(
            session,
            user,
            permission,
            AuthorizationDenialReason.MISSING_ROLE,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    if not isinstance(role, UserRole):
        _deny(
            session,
            user,
            permission,
            AuthorizationDenialReason.UNKNOWN_ROLE,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    if permission not in permissions_for_role(role):
        _deny(
            session,
            user,
            permission,
            AuthorizationDenialReason.PERMISSION_DENIED,
            resource_type=resource_type,
            resource_id=resource_id,
        )


def deny_resource_unavailable(
    session: Session | AsyncSession,
    user: User,
    permission: Permission,
    *,
    resource_type: str,
    resource_id: UUID,
) -> None:
    """Nega sem distinguir recurso inexistente de recurso pertencente a outro."""
    _deny(
        session,
        user,
        permission,
        AuthorizationDenialReason.RESOURCE_UNAVAILABLE,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def ai_profile_for_user(session: Session | AsyncSession, user: User) -> UserRole:
    """Seleciona o perfil interno exclusivamente a partir do papel persistido."""
    role = getattr(user, "role", None)
    permission_by_role = {
        UserRole.USER: Permission.AI_PROFILE_USER,
        UserRole.ADMIN: Permission.AI_PROFILE_ADMIN,
        UserRole.DEV: Permission.AI_PROFILE_DEV,
    }
    if not isinstance(role, UserRole):
        authorize(session, user, Permission.AI_INTERPRET)
        raise AssertionError("unreachable authorization state")
    authorize(session, user, permission_by_role[role])
    return role


def _deny(
    session: Session | AsyncSession,
    user: User,
    permission: Permission,
    reason: AuthorizationDenialReason,
    *,
    resource_type: str,
    resource_id: UUID | None,
) -> None:
    role = getattr(user, "role", None)
    safe_role = role.value if isinstance(role, UserRole) else "unknown"
    actor_id = getattr(user, "id", None)
    target_id = resource_id or actor_id
    if isinstance(actor_id, UUID) and isinstance(target_id, UUID):
        session.add(
            AuditEntry(
                actor_type="telegram",
                actor_id=actor_id,
                action="authorization.denied",
                resource_type=resource_type,
                resource_id=target_id,
                entry_metadata={
                    "permission": permission.value,
                    "reason": reason.value,
                    "role": safe_role,
                },
            )
        )
    raise AuthorizationDenied(permission, reason)
