"""Registro central dos modelos carregados pela metadata e pelo Alembic."""

from app.alerts.models import MissionProductAlertState
from app.audit.models import AuditEntry
from app.authentication.models import (
    AdminApiKey,
    CredentialActionToken,
    TelegramLinkToken,
    UserAuthSession,
    UserCredential,
    WebSession,
)
from app.collection.models import (
    CollectionRun,
    MissionOfferRelevance,
    PriceObservation,
)
from app.events.models import Event, EventConsumptionAttempt, EventDeliveryCheckpoint
from app.historical_bootstrap.models import ExternalPriceReference, HistoricalBootstrap
from app.market_research.models import MarketPriceAssessment
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionProductSelection,
    MissionSchedule,
    MissionSource,
    MissionTransition,
)
from app.offers.models import Offer, OfferShortLink
from app.products.models import Product
from app.purchase.models import PurchaseConfirmation, PurchaseTrailEntry
from app.stores.models import Seller, Store
from app.telegram.models import TelegramUpdateReceipt
from app.users.models import User

REGISTERED_MODELS = (
    CollectionRun,
    PriceObservation,
    MissionOfferRelevance,
    MarketPriceAssessment,
    HistoricalBootstrap,
    ExternalPriceReference,
    MissionProductAlertState,
    User,
    Product,
    Store,
    Seller,
    Offer,
    OfferShortLink,
    AuditEntry,
    Mission,
    MissionCriteria,
    MissionProductSelection,
    MissionTransition,
    MissionSource,
    MissionSchedule,
    Event,
    EventConsumptionAttempt,
    EventDeliveryCheckpoint,
    PurchaseConfirmation,
    PurchaseTrailEntry,
    UserCredential,
    UserAuthSession,
    CredentialActionToken,
    TelegramLinkToken,
    TelegramUpdateReceipt,
    WebSession,
    AdminApiKey,
)
