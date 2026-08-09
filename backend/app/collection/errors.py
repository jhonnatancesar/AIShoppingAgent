"""Erros estáveis da fronteira de coleta."""


class CollectionError(Exception):
    """Erro base da camada de coleta."""


class CollectionContractError(CollectionError, ValueError):
    """Um pedido ou resultado violou o contrato do adaptador."""


class DuplicateProviderError(CollectionError):
    """Mais de um provider declarou a mesma fonte."""


class UnsupportedSourceError(CollectionError):
    """A fonte pedida não possui provider registrado."""


class ProviderNavigationError(CollectionError):
    def __init__(self, source_code: str, status: int | None) -> None:
        self.source_code = source_code
        self.status = status
        super().__init__(f"{source_code} navigation failed with status {status}")


class ProviderBlockedError(CollectionError):
    def __init__(self, source_code: str, status: int | None) -> None:
        self.source_code = source_code
        self.status = status
        super().__init__(
            f"{source_code} blocked collection or changed markup (status {status})"
        )


class ProviderCircuitOpenError(CollectionError):
    def __init__(self, source_code: str) -> None:
        self.source_code = source_code
        super().__init__(f"{source_code} collection circuit is open")


class CollectionNormalizationError(CollectionError, ValueError):
    """Uma oferta bruta não possui representação monetária determinística."""
