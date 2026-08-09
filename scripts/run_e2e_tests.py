"""Executa o E2E reproduzível com o guard PostgreSQL da TASK-052."""

from pathlib import Path

from run_integration_tests import IntegrationRunnerError, run


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "tests" / "e2e"
    try:
        run([str(target)])
    except IntegrationRunnerError as error:
        raise SystemExit(f"E2E reproducible failed: {error}") from error


if __name__ == "__main__":
    main()
