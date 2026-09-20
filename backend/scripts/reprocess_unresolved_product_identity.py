"""Backfill de identidade global de produto via aprendizado assistido por
IA (TASK-123) -- achado real: placas-mãe (e qualquer outra categoria sem
extrator determinístico) nunca ganharam `category`/`identity_key`,
porque `identity_learning.reprocess_unresolved_products` -- já
implementada e testada nos checkpoints 3-11 -- nunca tinha nenhum
chamador em todo o repositório.

Determinístico e auditável na parte que decide O QUE processar (mesma
consulta de `reprocess_unresolved_products`: `Product.identity_key IS
NULL`, sem scan de todo o banco); a extração em si usa IA (sempre com
grounding anti-alucinação, `identity_ai._is_grounded` -- nunca inventa
campo ausente do título). Nunca duplica `Product`: quando a identidade
resolvida já corresponde a um `Product` canônico existente, todas as
`Offer`s do ad-hoc migram pra lá e o ad-hoc é removido; quando não
existe ainda, o próprio ad-hoc é promovido no lugar -- nenhuma `Offer`/
`PriceObservation` já coletada é perdida (ver `apply_learned_identity`,
`app/products/identity_learning.py`).

Não depende de `settings.product_identity_learning_enabled` (essa flag
só afeta o caminho de coleta ao vivo, `app/collection/orchestration.py`)
-- este script constrói seu próprio `AIProviderManager` explícito,
igual aos demais scripts de manutenção/reparo deste diretório.

`ai_manager` roda sempre em `UserRole.ADMIN` (nunca `USER`, mesma regra
de validação já usada por outros scripts operacionais deste projeto);
`arbiter_ai_manager` é um manager SEPARADO, construído com
`build_user_ai_provider_manager` -- reutilizar o manager ADMIN/DEV para
o árbitro de identidade é o bug real já encontrado e corrigido no
checkpoint 4 (`app.collection.worker.run_worker` segue o mesmo padrão).

Custo de IA (achado de 2026-09-20, pergunta real do usuário): cada
Product sem `identity_key` já falhou o motor determinístico E não tem
candidato aprovado reaproveitável -- então, por definição, resolver o
backlog atual GASTA uma chamada de IA real por família/modelo
genuinamente novo (só reaproveita sem custo quando duas Offers
diferentes resolvem pro MESMO candidato já aprendido nesta mesma
rodada). `--dry-run`/`--apply` com `--limit` NUNCA mostravam o TOTAL
real do backlog, só os primeiros `limit` -- corrigido: agora sempre
imprime o total antes de decidir qualquer coisa. `--count-only` conta
o backlog sem tentar resolver nada (zero custo de IA, seguro rodar a
qualquer momento para saber o tamanho real antes de decidir o ritmo).

Lote de IA (mesma pergunta do usuário, correção 2026-09-20): em vez de
1 chamada de IA por Product (depois de tentar o motor determinístico/
cache/reuso sem IA para cada um), `reprocess_unresolved_products` agora
agrupa só os produtos que realmente precisam de extração em lotes de
`_BATCH_SIZE` (4) títulos por chamada -- corta o número de chamadas
de IA por ~4x. Por isso `--limit`/`--dry-run`/`--apply` têm um teto
rígido de `_MAX_LIMIT` (20 Products, 5 lotes de 4) -- este script é
para o backlog pequeno já conhecido, não para volume alto sem
supervisão; rode em rodadas de até 20 até esgotar o backlog.

Uso:
    python -m scripts.reprocess_unresolved_product_identity --count-only
    python -m scripts.reprocess_unresolved_product_identity --dry-run
    python -m scripts.reprocess_unresolved_product_identity --dry-run --limit 20
    python -m scripts.reprocess_unresolved_product_identity --apply --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.ai_provider import (
    build_admin_dev_ai_provider_manager,
    build_user_ai_provider_manager,
)
from app.core.config import get_settings
from app.database.session import (
    create_async_database_engine,
    create_async_session_factory,
)
from app.products.identity_learning import reprocess_unresolved_products
from app.products.models import Product
from app.users.models import UserRole
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_BATCH_SIZE = 4
_MAX_LIMIT = 20


async def _total_unresolved_count(session: AsyncSession) -> int:
    """Total REAL do backlog, sem `limit` -- nunca capado, ao contrário
    da lista de candidatos abaixo. Zero custo de IA: só `COUNT(*)`."""
    return await session.scalar(
        select(func.count()).select_from(Product).where(Product.identity_key.is_(None))
    )


async def _unresolved_candidates(session: AsyncSession, limit: int) -> list[Product]:
    """Mesma consulta usada internamente por `reprocess_unresolved_products`
    -- repetida aqui só para reportar os candidatos ANTES de processar
    (a função original não expõe essa lista, só o total resolvido)."""
    rows = (
        await session.scalars(
            select(Product).where(Product.identity_key.is_(None)).limit(limit)
        )
    ).all()
    return list(rows)


async def run(
    *,
    apply: bool,
    limit: int,
    count_only: bool = False,
    ai_manager: object | None = None,
    arbiter_ai_manager: object | None = None,
) -> None:
    """`ai_manager`/`arbiter_ai_manager` só são parâmetros para permitir
    injetar um fake em teste (mesmo padrão de `sleep`/`monotonic`
    injetáveis já usado no Coupon Worker) -- a execução real via `main()`
    nunca os passa, sempre constrói os managers de verdade abaixo."""
    settings = get_settings()
    async_driver = "asyncpg" if sys.platform == "win32" else "psycopg"
    engine = create_async_database_engine(settings, async_driver=async_driver)
    session_factory = create_async_session_factory(engine)
    async with session_factory() as session:
        total = await _total_unresolved_count(session)
        print(f"Total REAL de Products sem identity_key no banco: {total}")
        if count_only:
            print("--count-only: nenhuma tentativa de resolver, zero chamada de IA.")
            await session.rollback()
            await engine.dispose()
            return

        candidates = await _unresolved_candidates(session, limit)
        print(
            f"Candidatos desta rodada (limit={limit}): {len(candidates)} de {total} no total"
        )
        for product in candidates:
            print(f"  candidato {product.id}: '{product.name[:80]}'")
        if not candidates:
            print("Nada a fazer.")
            await session.rollback()
            await engine.dispose()
            return
        candidate_ids = [product.id for product in candidates]

        if ai_manager is None:
            ai_manager = build_admin_dev_ai_provider_manager(settings)
        if arbiter_ai_manager is None:
            arbiter_ai_manager = build_user_ai_provider_manager(settings)

        resolved_count = await reprocess_unresolved_products(
            session,
            ai_manager=ai_manager,
            profile=UserRole.ADMIN,
            arbiter_ai_manager=arbiter_ai_manager,
            limit=limit,
            batch_size=_BATCH_SIZE,
            apply=apply,
        )
        print(
            f"Resolvidos nesta rodada: {resolved_count} de {len(candidates)} candidatos "
            f"(lote de até {_BATCH_SIZE} títulos por chamada de IA)"
        )

        for product_id in candidate_ids:
            current = await session.get(Product, product_id)
            if current is None:
                print(
                    f"  {product_id}: Offers migradas para um Product canônico "
                    "já existente (ad-hoc removido, nenhuma Offer perdida)"
                )
            elif current.identity_key is not None:
                print(
                    f"  {product_id}: RESOLVIDO -> category={current.category} "
                    f"brand={current.brand} family={current.family} "
                    f"model={current.model} variant={current.variant}"
                )
            else:
                print(
                    f"  {product_id}: continua sem identidade (extração da IA "
                    "falhou, não passou no grounding, ou foi para revisão "
                    "humana -- ver product_identity_candidates.status)"
                )

        if apply:
            await session.commit()
            print("Aplicado.")
        else:
            await session.rollback()
            print(
                "Dry-run -- nada foi persistido (inclusive candidatos de IA "
                "gravados em product_identity_candidates foram descartados)."
            )
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--count-only",
        action="store_true",
        help="Só conta o backlog real (sem limit, sem tentar resolver nada) -- "
        "zero chamada de IA. Rode isto primeiro.",
    )
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=_MAX_LIMIT,
        help=f"Teto de Products processados nesta rodada (default/máximo: "
        f"{_MAX_LIMIT}, ignorado com --count-only) -- lote de {_BATCH_SIZE} "
        "títulos por chamada de IA. Rode de novo para processar o restante "
        "do backlog.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit precisa ser positivo")
    if args.limit > _MAX_LIMIT:
        parser.error(f"--limit não pode passar de {_MAX_LIMIT} nesta rodada")
    asyncio.run(run(apply=args.apply, limit=args.limit, count_only=args.count_only))


if __name__ == "__main__":
    main()
