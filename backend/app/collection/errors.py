"""Erros estáveis da fronteira de coleta."""


class CollectionError(Exception):
    """Erro base da camada de coleta."""


class CollectionContractError(CollectionError, ValueError):
    """Um pedido ou resultado violou o contrato do adaptador."""


class DuplicateProviderError(CollectionError):
    """Mais de um provider declarou a mesma fonte."""


class UnsupportedSourceError(CollectionError):
    """A fonte pedida não possui provider registrado."""
