"""Registro central dos modelos carregados pela metadata e pelo Alembic."""

from app.audit.models import AuditEntry
from app.missions.models import Mission, MissionCriteria, MissionTransition
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User

REGISTERED_MODELS = (
    User,
    Product,
    Store,
    Offer,
    AuditEntry,
    Mission,
    MissionCriteria,
    MissionTransition,
)
