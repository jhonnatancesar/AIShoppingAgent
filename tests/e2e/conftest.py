"""Reutiliza o banco clonado e fail-closed da suíte de integração."""

from tests.integration.conftest import IntegrationDatabase, integration_database

__all__ = ("IntegrationDatabase", "integration_database")
