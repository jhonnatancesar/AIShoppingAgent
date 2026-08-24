"""Descoberta do Microsoft Edge instalado na máquina.

Compartilhada por `EdgeCdpSupervisor` (`app/collection/providers/`) e
`BrowserSession` (`app/collection/browser.py`, TASK-109 fechamento --
testes locais também lançam o Edge real, nunca Chromium). Mora fora do
pacote `providers` de propósito: `browser.py` não é um provider e não
pode depender de `app.collection.providers` sem criar import circular
(`providers/__init__.py` importa `stores.py` -> `base.py` -> `browser.py`).
"""

import os
from pathlib import Path


class EdgeExecutableNotFoundError(RuntimeError):
    """Nenhuma instalação normal do Microsoft Edge foi encontrada."""


def discover_edge_executable(explicit_path: Path | None = None) -> Path:
    """Localiza somente instalações normais do Microsoft Edge."""
    if explicit_path is not None:
        candidate = explicit_path.expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise EdgeExecutableNotFoundError("configured Edge executable was not found")

    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(
                Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise EdgeExecutableNotFoundError("Microsoft Edge executable was not found")
