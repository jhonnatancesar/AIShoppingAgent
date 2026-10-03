"""Importa fontes abertas copiadas (TASK-137) para o catálogo de identidade.

BuildCores OpenDB (ODC-By 1.0), TODAS as pastas de produto (placa-mãe e RAM com
regras próprias; as demais por `identity_catalog_open_all.FOLDER_SPECS`). Lê arquivos
JSON já baixados (cópia da base; nenhuma chamada de rede). Sempre idempotente e sem IA.
Códigos ambíguos são descartados considerando TODAS as pastas juntas. `--dry-run`
calcula tudo e desfaz; `--apply` grava.

Atribuição obrigatória da fonte: ver rodapé "Fontes de dados" do site.

Uso (a partir de `backend/`):
    python -m scripts.import_open_catalog --path <pasta open-db> --dry-run
    python -m scripts.import_open_catalog --path <pasta open-db> --apply
    python -m scripts.import_open_catalog --path <pasta open-db> --apply --folders GPU CPU
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
from collections import defaultdict
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
)
from app.products.identity_catalog_open_all import (
    FOLDER_SPECS,
    map_buildcores_record,
    map_wikidata_device,
)

_Mapper = Callable[[Any, MappingReport], OpenCatalogEntry | None]


def _mappers() -> dict[str, _Mapper]:
    mappers: dict[str, _Mapper] = {
        "Motherboard": map_buildcores_motherboard,
        "RAM": map_buildcores_ram,
    }
    for folder in FOLDER_SPECS:
        mappers[folder] = lambda record, report, _f=folder: map_buildcores_record(
            _f, record, report
        )
    return mappers


def _read_folder(
    root: Path, folder: str, mapper: _Mapper
) -> tuple[list[OpenCatalogEntry], MappingReport]:
    report = MappingReport()
    entries: list[OpenCatalogEntry] = []
    for path in sorted((root / folder).glob("*.json")):
        entry = mapper(json.loads(path.read_text(encoding="utf-8")), report)
        if entry is not None:
            entries.append(entry)
    return entries, report


def _read_wikidata(
    wikidata: Path | None,
) -> tuple[list[OpenCatalogEntry], dict[str, MappingReport]]:
    entries: list[OpenCatalogEntry] = []
    reports: dict[str, MappingReport] = {}
    if wikidata is None:
        return entries, reports
    for kind in ("smartphone", "smartwatch"):
        path = wikidata / f"{kind}.json"
        if not path.is_file():
            continue
        report = MappingReport()
        for record in json.loads(path.read_text(encoding="utf-8")):
            entry = map_wikidata_device(kind, record, report)
            if entry is not None:
                entries.append(entry)
        reports[f"wikidata:{kind}"] = report
    return entries, reports


async def _run(
    root: Path, apply: bool, only: list[str] | None, wikidata: Path | None
) -> int:
    mappers = _mappers()
    folders = [f for f in mappers if only is None or f in only]
    missing = [f for f in folders if not (root / f).is_dir()]
    if missing:
        print(f"pastas não encontradas em {root}: {missing}", file=sys.stderr)
        return 2
    everything: list[OpenCatalogEntry] = []
    reports: dict[str, MappingReport] = {}
    for folder in folders:
        entries, report = _read_folder(root, folder, mappers[folder])
        everything.extend(entries)
        reports[folder] = report
    phones, phone_reports = _read_wikidata(wikidata)
    everything.extend(phones)
    reports.update(phone_reports)
    everything = merge_same_identity(everything)
    global_report = MappingReport()
    global_report.mapped = len(everything)
    everything = drop_ambiguous_codes(everything, global_report)

    for folder, report in reports.items():
        reasons = ", ".join(f"{r}={c}" for r, c in sorted(report.skipped.items()))
        print(
            f"{folder:16} lidos={report.read:5} aproveitados={report.mapped:5}  {reasons}"
        )
    by_category: dict[str, int] = defaultdict(int)
    for entry in everything:
        by_category[entry.category] += 1
    print(
        f"\nTOTAL entradas={len(everything)} códigos ambíguos descartados="
        f"{global_report.dropped_ambiguous_codes} descartadas só com código ambíguo="
        f"{global_report.skipped.get('so_tinha_codigo_ambiguo', 0)}"
    )
    print("por categoria:", dict(sorted(by_category.items())))

    engine = create_async_database_engine(get_settings())
    sessions = create_async_session_factory(engine)
    try:
        async with sessions() as session:
            stats = await import_open_entries(session, everything)
            if apply:
                await session.commit()
            else:
                await session.rollback()
        print(
            f"entradas novas={stats.entries_new} já existiam={stats.entries_existing} "
            f"inválidas={stats.entries_invalid} códigos novos={stats.codes_new} "
            f"conflitos={stats.codes_conflict}"
        )
        print("GRAVADO" if apply else "SIMULAÇÃO (nada gravado)")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, help="pasta open-db")
    parser.add_argument("--folders", nargs="*", help="só estas pastas (padrão: todas)")
    parser.add_argument(
        "--wikidata",
        type=Path,
        help="pasta com smartphone.json/smartwatch.json exportados do Wikidata (CC0)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return asyncio.run(
        _run(args.path, apply=args.apply, only=args.folders, wikidata=args.wikidata),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
