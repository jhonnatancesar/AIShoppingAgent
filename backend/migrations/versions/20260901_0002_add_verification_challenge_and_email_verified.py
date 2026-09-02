"""Cria verification_challenges e email_verified_at (Subtask 9, auditoria GG Oferta).

Revision ID: 20260901_0002
Revises: 20260901_0001

`VerificationChallenge` é a infraestrutura compartilhada de código curto
(purpose x channel) para recuperação/alteração de senha e verificação de
e-mail -- distinta de `CredentialActionToken` (token longo de link,
sempre amarrado a `telegram_user_id`, usado só pelo fluxo `/auth`
existente): aqui o código é curto o bastante para digitar, funciona sem
Telegram (canal `email`) e nunca concede sessão sozinho -- só autoriza a
troca de senha ou marca `email_verified_at` quando confirmado.

`email_verified_at` é timestamp (não booleano), mesmo padrão já usado em
`User.deleted_at` -- nasce `NULL` para todo usuário existente e só é
preenchido por uma confirmação real de `email_verification`/`email`.

`users.email` nunca teve `UNIQUE` (só `username` tinha, achado do
preflight) -- o cadastro Web self-service exige que colisão de e-mail
nunca crie uma segunda conta, inclusive sob concorrência real (só uma
constraint de banco garante isso; checagem em aplicação sozinha tem
corrida). Mesmo padrão exato de `uq_users_username`
(`20260808_0001_add_registration_fields_to_users.py`): uma
`UNIQUE CONSTRAINT` simples, não um índice parcial -- `UNIQUE` do
Postgres já trata múltiplos `NULL` como não conflitantes por padrão, sem
precisar de `WHERE email IS NOT NULL`.

Estratégia de unicidade: "e-mail sempre armazenado normalizado", não
índice case-insensitive -- os três pontos de escrita de `email`
(`app.users.service.create_user_with_password[_async]`,
`app.users.registration.validate_email`,
`app.webapp.account_router.AccountProfileUpdate.normalize_email`) agora
gravam sempre `strip().lower()`, então uma `UNIQUE CONSTRAINT` simples
sobre a coluna já é suficiente -- nenhuma variação de maiúsculas chega a
ser persistida depois desta subtask.

Dado real de auditoria (achado ao vivo tentando aplicar esta migration
contra dados legados sintéticos representativos, ver
`tests/integration/test_email_uniqueness_migration.py`): um `ALTER TABLE
... ADD CONSTRAINT UNIQUE` batendo numa duplicata pré-existente falha com
um `UniqueViolation` cru do Postgres -- inaceitável descobrir isso no
meio de um deploy. Por isso, antes de criar a constraint, este `upgrade()`
primeiro (1) normaliza (`lower(btrim(email))`) todo `email` legado --
resolve sozinho os casos de diferença só de maiúsculas/minúsculas ou
espaço nas pontas -- e (2) verifica se ainda sobra alguma duplicata
*verdadeira* (mesmo endereço, já normalizado, em 2+ contas) e, se sobrar,
aborta com um erro claro e acionável nomeando os e-mails colididos, em vez
de deixar o Postgres estourar um `UniqueViolation` sem contexto. Contas
`NULL` (ex.: origem Telegram sem e-mail) nunca colidem entre si -- mesmo
comportamento de `uq_users_username`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0002"
down_revision: str | None = "20260901_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE users SET email = lower(btrim(email)) "
            "WHERE email IS NOT NULL AND email <> lower(btrim(email))"
        )
    )
    remaining_duplicates = bind.execute(
        sa.text(
            "SELECT email, count(*) AS total FROM users "
            "WHERE email IS NOT NULL GROUP BY email HAVING count(*) > 1 "
            "ORDER BY email"
        )
    ).fetchall()
    if remaining_duplicates:
        details = ", ".join(
            f"{row.email} ({row.total}x)" for row in remaining_duplicates
        )
        raise RuntimeError(
            "Não é possível aplicar uq_users_email: existem contas com o "
            "mesmo e-mail (já normalizado para minúsculas, sem espaço nas "
            f"pontas): {details}. Resolva manualmente qual conta deve "
            "manter cada e-mail (a outra fica com email=NULL ou é "
            "mesclada) antes de reexecutar esta migration -- ela não "
            "decide isso sozinha."
        )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_table(
        "verification_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('password_reset', 'password_change', 'email_verification')",
            name="ck_verification_challenges_purpose_values",
        ),
        sa.CheckConstraint(
            "channel IN ('telegram', 'email')",
            name="ck_verification_challenges_channel_values",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_verification_challenges_attempts_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_verification_challenges_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_challenges")),
    )
    op.create_index(
        "ix_verification_challenges_user_purpose",
        "verification_challenges",
        ["user_id", "purpose", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verification_challenges_user_purpose",
        table_name="verification_challenges",
    )
    op.drop_table("verification_challenges")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "email_verified_at")
