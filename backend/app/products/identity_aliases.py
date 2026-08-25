"""Carrega aliases persistidos (TASK-112) no formato que
`app.products.identity.resolve_monitoring_identity` espera.

Módulo separado de propósito: `app.products.identity` continua sem
nenhuma dependência de sessão/ORM (ver docstring do módulo) -- mesmo
padrão já usado por `app.collection.queue_config.resolve_queue_config`,
uma função pura que recebe o dado já carregado, não uma sessão.
"""

from collections.abc import Iterable

from app.products.models import ProductIdentityAlias

AliasMapping = dict[tuple[str, str, str], str]


def build_alias_mapping(rows: Iterable[ProductIdentityAlias]) -> AliasMapping:
    """Só `status="active"` participa -- candidatos ainda não promovidos
    (TASK-112 §4) nunca influenciam `monitoring_key`."""
    return {
        (row.category, row.attribute_name, row.raw_value_normalized): row.canonical_value
        for row in rows
        if row.status == "active"
    }
