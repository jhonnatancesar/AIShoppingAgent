"""Registro central dos modelos carregados pela metadata e pelo Alembic."""

from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.collection.models import (
    CollectionRun,
    MissionOfferRelevance,
    PriceObservation,
)
from app.events.models import Event, EventConsumptionAttempt
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSchedule,
    MissionSource,
    MissionTransition,
)
from app.offers.models import Offer
from app.products.models import Product
from app.purchase.models import PurchaseConfirmation, PurchaseTrailEntry
from app.stores.models import Seller, Store
from app.telegram.models import TelegramUpdateReceipt
from app.users.models import User

REGISTERED_MODELS = (
    CollectionRun,
    PriceObservation,
    MissionOfferRelevance,
    User,
    Product,
    Store,
    Seller,
    Offer,
    AuditEntry,
    Mission,
    MissionCriteria,
    MissionTransition,
    MissionSource,
    MissionSchedule,
    Event,
    EventConsumptionAttempt,
    PurchaseConfirmation,
    PurchaseTrailEntry,
    UserCredential,
    UserAuthSession,
    CredentialActionToken,
    TelegramUpdateReceipt,
)
