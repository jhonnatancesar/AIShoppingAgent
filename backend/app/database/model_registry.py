"""Registro central dos modelos carregados pela metadata e pelo Alembic."""

from app.products.models import Product
from app.users.models import User

REGISTERED_MODELS = (User, Product)
