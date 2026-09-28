"""TASK-129: a tabela de grafias (`product_identity_aliases`) existia desde a
TASK-112, mas nasceu vazia e a extração por IA não a consultava. Achado
real do dry-run da PROD (2026-09-27): a IA escrevia a linha no lugar do
fabricante ("Fury" x "Kingston", "ROG" x "ASUS") e o mesmo produto virava
dois. Esta migration:

1. semeia a lista inicial de linhas de produto -> fabricante (`active`);
2. aceita o status `rejected` -- sugestão recusada na revisão fica
   guardada, para a mesma sugestão nunca voltar.

`category="*"` vale para qualquer categoria (linha que o fabricante usa
em vários tipos de produto); o resto é por categoria, quando o nome da
linha é ambíguo fora dela (ex.: "Nitro" é Acer em notebook/monitor, mas
Sapphire em placa de vídeo). Grafias em slug, a mesma forma da identidade.

Revision ID: 20260927_0001
Revises: 20260926_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0001"
down_revision: str | None = "20260926_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "ck_product_identity_aliases_status_values"

# (categoria ou "*", campo, grafia alternativa, grafia oficial)
SEED_ALIASES: tuple[tuple[str, str, str, str], ...] = (
    ("*", "brand", "rog", "asus"),
    ("*", "brand", "rog-strix", "asus"),
    ("*", "brand", "asus-rog", "asus"),
    ("*", "brand", "republic-of-gamers", "asus"),
    ("*", "brand", "tuf", "asus"),
    ("*", "brand", "tuf-gaming", "asus"),
    ("*", "brand", "asustek", "asus"),
    ("*", "brand", "vivobook", "asus"),
    ("*", "brand", "zenbook", "asus"),
    ("*", "brand", "aorus", "gigabyte"),
    ("*", "brand", "gigabyte-aorus", "gigabyte"),
    ("*", "brand", "micro-star", "msi"),
    ("*", "brand", "kingston-fury", "kingston"),
    ("ram", "brand", "fury", "kingston"),
    ("ssd", "brand", "fury", "kingston"),
    ("ram", "brand", "hyperx", "kingston"),
    ("ram", "brand", "vengeance", "corsair"),
    ("ram", "brand", "dominator", "corsair"),
    ("ram", "brand", "trident-z", "g-skill"),
    ("ram", "brand", "ripjaws", "g-skill"),
    ("*", "brand", "gskill", "g-skill"),
    ("*", "brand", "xpg", "adata"),
    ("*", "brand", "t-force", "teamgroup"),
    ("*", "brand", "team-group", "teamgroup"),
    ("*", "brand", "wd", "western-digital"),
    ("*", "brand", "wd-black", "western-digital"),
    ("*", "brand", "wd-blue", "western-digital"),
    ("*", "brand", "wd-green", "western-digital"),
    ("*", "brand", "coolermaster", "cooler-master"),
    ("*", "brand", "iphone", "apple"),
    ("*", "brand", "ipad", "apple"),
    ("*", "brand", "macbook", "apple"),
    ("*", "brand", "galaxy", "samsung"),
    ("monitor", "brand", "odyssey", "samsung"),
    ("*", "brand", "redmi", "xiaomi"),
    ("*", "brand", "poco", "xiaomi"),
    ("*", "brand", "logitech-g", "logitech"),
    ("*", "brand", "ultragear", "lg"),
    ("*", "brand", "lg-ultragear", "lg"),
    ("*", "brand", "predator", "acer"),
    ("notebook", "brand", "nitro", "acer"),
    ("monitor", "brand", "nitro", "acer"),
    ("*", "brand", "legion", "lenovo"),
    ("*", "brand", "ideapad", "lenovo"),
    ("*", "brand", "thinkpad", "lenovo"),
    ("*", "brand", "omen", "hp"),
    ("*", "brand", "victus", "hp"),
)


def upgrade() -> None:
    op.drop_constraint(_STATUS_CHECK, "product_identity_aliases", type_="check")
    op.create_check_constraint(
        _STATUS_CHECK,
        "product_identity_aliases",
        "status IN ('active', 'candidate', 'rejected')",
    )
    insert = sa.text(
        "INSERT INTO product_identity_aliases "
        "(id, category, attribute_name, raw_value_normalized, canonical_value, "
        "status) VALUES (gen_random_uuid(), :category, :attribute, :raw, "
        ":canonical, 'active') "
        "ON CONFLICT (category, attribute_name, raw_value_normalized) DO NOTHING"
    )
    connection = op.get_bind()
    for category, attribute, raw, canonical in SEED_ALIASES:
        connection.execute(
            insert,
            {
                "category": category,
                "attribute": attribute,
                "raw": raw,
                "canonical": canonical,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    delete = sa.text(
        "DELETE FROM product_identity_aliases WHERE category = :category "
        "AND attribute_name = :attribute AND raw_value_normalized = :raw "
        "AND canonical_value = :canonical"
    )
    for category, attribute, raw, canonical in SEED_ALIASES:
        connection.execute(
            delete,
            {
                "category": category,
                "attribute": attribute,
                "raw": raw,
                "canonical": canonical,
            },
        )
    # O status antigo não conhece `rejected`: a recusa sai junto.
    connection.execute(
        sa.text("DELETE FROM product_identity_aliases WHERE status = 'rejected'")
    )
    op.drop_constraint(_STATUS_CHECK, "product_identity_aliases", type_="check")
    op.create_check_constraint(
        _STATUS_CHECK,
        "product_identity_aliases",
        "status IN ('active', 'candidate')",
    )
