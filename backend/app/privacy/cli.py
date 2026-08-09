"""Operações manuais e controladas de privacidade da V1."""

import argparse
from datetime import UTC, datetime
from getpass import getpass
from uuid import UUID

from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.privacy.service import (
    HistoricalPersonalDataConflict,
    PrivacyOperationError,
    cleanup_expired_authentication_artifacts,
    deidentify_account,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    cleanup_parser = subparsers.add_parser("cleanup-auth")
    cleanup_parser.add_argument("--execute", action="store_true", required=True)
    deidentify_parser = subparsers.add_parser("deidentify-account")
    deidentify_parser.add_argument("--user-id", type=UUID, required=True)
    deidentify_parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()

    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with session_factory.begin() as session:
            if args.operation == "cleanup-auth":
                result = cleanup_expired_authentication_artifacts(
                    session, now=datetime.now(UTC)
                )
                print(
                    "cleanup concluído: "
                    f"tokens={result.action_tokens_deleted}, "
                    f"sessions={result.auth_sessions_deleted}"
                )
                return
            try:
                expected_id = int(getpass("Telegram user ID atual (entrada oculta): "))
            except ValueError:
                raise SystemExit("operação recusada: identidade inválida") from None
            confirmation = input("Digite DESIDENTIFICAR para confirmar: ").strip()
            if confirmation != "DESIDENTIFICAR":
                raise SystemExit("operação cancelada")
            result = deidentify_account(
                session,
                user_id=args.user_id,
                expected_telegram_user_id=expected_id,
            )
            print(
                "desidentificação concluída: "
                f"missions={result.missions_scrubbed}, "
                f"credentials={result.credentials_deleted}, "
                f"tokens={result.action_tokens_deleted}, "
                f"sessions={result.auth_sessions_deleted}, "
                f"already_done={str(result.already_deidentified).lower()}"
            )
    except HistoricalPersonalDataConflict as error:
        raise SystemExit(
            "operação recusada: PII encontrada em histórico imutável ("
            + ", ".join(error.areas)
            + ")"
        ) from None
    except PrivacyOperationError as error:
        raise SystemExit(f"operação recusada: {type(error).__name__}") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
