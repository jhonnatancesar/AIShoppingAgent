"""Importa fontes abertas copiadas (TASK-137) para o catálogo de identidade.

Hoje: BuildCores OpenDB (ODC-By 1.0), pastas `Motherboard` e `RAM`. Lê arquivos
JSON já baixados (cópia da base; nenhuma chamada de rede). Sempre idempotente e
sem IA. `--dry-run` calcula tudo e desfaz; `--apply` grava.

Atribuição obrigatória da fonte: ver rodapé "Fontes de dados" do site.

Uso (a partir de `backend/`):
    python -m scripts.import_open_catalog --path <pasta open-db> --dry-run
    python -m scripts.import_open_catalog --path <pasta open-db> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.database.session import (
    create_async_database_engine,
    create_async_session_factory,
)
from app.products.identity_catalog_import import (
    MappingReport,
    OpenCatalogEntry,
    drop_ambiguous_codes,
    import_open_entries,
    map_buildcores_motherboard,
    map_buildcores_ram,
    merge_same_identity,
    merge_shared_part_numbers,
)

_FOLDERS: dict[str, Callable[[Any, MappingReport], OpenCatalogEntry | None]] = {
    "Motherboard": map_buildcores_motherboard,
    "RAM": map_buildcores_ram,
}


def _map_folder(
    root: Path, folder: str
) -> tuple[list[OpenCatalogEntry], MappingReport]:
    report = MappingReport()
    mapper = _FOLDERS[folder]
    entries: list[OpenCatalogEntry] = []
    for path in sorted((root / folder).glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        entry = mapper(record, report)
        if entry is not None:
            entries.append(entry)
    entries = merge_same_identity(entries)
    entries = merge_shared_part_numbers(entries)
    entries = drop_ambiguous_codes(entries, report)
    return entries, report


async def _run(root: Path, apply: bool) -> int:
    engine = create_async_database_engine(get_settings())
    sessions = create_async_session_factory(engine)
    try:
        for folder in _FOLDERS:
            if not (root / folder).is_dir():
                print(f"{folder}: pasta não encontrada em {root}", file=sys.stderr)
                return 2
            entries, report = _map_folder(root, folder)
            print(f"== {folder}")
            print(
                f"   lidos={report.read} aproveitados={report.mapped} "
                f"entradas={len(entries)} codigos_ambiguos_descartados="
                f"{report.dropped_ambiguous_codes}"
            )
            for reason, count in sorted(report.skipped.items()):
                print(f"   descartados ({reason}): {count}")
            async with sessions() as session:
                stats = await import_open_entries(session, entries)
                if apply:
                    await session.commit()
                else:
                    await session.rollback()
            print(
                f"   entradas novas={stats.entries_new} já existiam="
                f"{stats.entries_existing} inválidas={stats.entries_invalid} "
                f"códigos novos={stats.codes_new} conflitos={stats.codes_conflict}"
            )
        print("GRAVADO" if apply else "SIMULAÇÃO (nada gravado)")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, help="pasta open-db")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return asyncio.run(
        _run(args.path, apply=args.apply),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
