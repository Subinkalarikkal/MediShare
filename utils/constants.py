"""Shared enumerations, workflow state machines and default thresholds."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


# ---------------------------------------------------------------- roles
class Role(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    INSTITUTIONAL_DONOR = "INSTITUTIONAL_DONOR"
    NGO = "NGO"
    COLLECTION_CENTRE = "COLLECTION_CENTRE"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


ROLE_LABELS = {
    Role.INDIVIDUAL: "Individual / Household",
    Role.INSTITUTIONAL_DONOR: "Institutional Donor",
    Role.NGO: "NGO / Healthcare Organization",
    Role.COLLECTION_CENTRE: "Collection Centre",
    Role.REVIEWER: "Authorized Reviewer / Pharmacist",
    Role.ADMIN: "Administrator",
    Role.SUPER_ADMIN: "Super Administrator",
}

ORG_ROLES = {Role.INSTITUTIONAL_DONOR, Role.NGO, Role.COLLECTION_CENTRE}


class AccountStatus(StrEnum):
    PENDING = "PENDING"  # awaiting review – restricted access
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


# ---------------------------------------------------------------- permissions
class Perm(StrEnum):
    # household
    MEDICINE_REGISTER = "medicine.register"
    COLLECTION_REQUEST_CREATE = "collection.request.create"
    # organization
    ORG_MANAGE_OWN = "org.manage_own"
    INVENTORY_MANAGE = "inventory.manage"
    INSTITUTIONAL_REQUEST_MANAGE = "institutional_request.manage"
    # collection centre
    CENTRE_OPERATE = "centre.operate"
    DISPOSAL_RECORD = "disposal.record"
    # reviewer
    REVIEW_INVENTORY = "review.inventory"
    TRANSFER_AUTHORIZE_QUALITY = "transfer.authorize_quality"
    # admin
    USERS_MANAGE = "users.manage"
    ORG_VERIFY = "org.verify"
    CENTRE_VERIFY = "centre.verify"
    MONITOR_ALL = "monitor.all"
    ANALYTICS_VIEW = "analytics.view"
    AUDIT_VIEW = "audit.view"
    NOTIFICATIONS_MANAGE = "notifications.manage"
    TRANSFER_DOCUMENT = "transfer.document"
    MEDICINE_MASTER_MANAGE = "medicine_master.manage"
    RISK_RUN = "risk.run"
    AI_ASSISTANT = "ai.assistant"
    # super admin
    ADMINS_MANAGE = "admins.manage"
    PERMISSIONS_MANAGE = "permissions.manage"
    SETTINGS_MANAGE = "settings.manage"
    AUDIT_MANAGE = "audit.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"


_ADMIN_PERMS = [
    Perm.USERS_MANAGE, Perm.ORG_VERIFY, Perm.CENTRE_VERIFY, Perm.MONITOR_ALL,
    Perm.ANALYTICS_VIEW, Perm.AUDIT_VIEW, Perm.NOTIFICATIONS_MANAGE,
    Perm.TRANSFER_DOCUMENT, Perm.MEDICINE_MASTER_MANAGE, Perm.RISK_RUN, Perm.AI_ASSISTANT,
]

DEFAULT_ROLE_PERMISSIONS: dict[Role, list[Perm]] = {
    Role.INDIVIDUAL: [Perm.MEDICINE_REGISTER, Perm.COLLECTION_REQUEST_CREATE],
    Role.INSTITUTIONAL_DONOR: [Perm.ORG_MANAGE_OWN, Perm.INVENTORY_MANAGE],
    Role.NGO: [Perm.ORG_MANAGE_OWN, Perm.INSTITUTIONAL_REQUEST_MANAGE],
    Role.COLLECTION_CENTRE: [Perm.ORG_MANAGE_OWN, Perm.CENTRE_OPERATE, Perm.DISPOSAL_RECORD],
    Role.REVIEWER: [Perm.REVIEW_INVENTORY, Perm.TRANSFER_AUTHORIZE_QUALITY],
    Role.ADMIN: list(_ADMIN_PERMS),
    Role.SUPER_ADMIN: list(_ADMIN_PERMS) + [
        Perm.ADMINS_MANAGE, Perm.PERMISSIONS_MANAGE, Perm.SETTINGS_MANAGE,
        Perm.AUDIT_MANAGE, Perm.INTEGRATIONS_MANAGE,
    ],
}


# ---------------------------------------------------------------- organizations
class OrgType(StrEnum):
    HOSPITAL = "HOSPITAL"
    PHARMACY = "PHARMACY"
    HEALTHCARE_INSTITUTION = "HEALTHCARE_INSTITUTION"
    NGO = "NGO"
    CHARITABLE_HEALTHCARE = "CHARITABLE_HEALTHCARE"
    COLLECTION_CENTRE = "COLLECTION_CENTRE"


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


VERIFICATION_TRANSITIONS = {
    "PENDING": {"UNDER_REVIEW", "REJECTED"},
    "UNDER_REVIEW": {"VERIFIED", "REJECTED", "PENDING"},
    "VERIFIED": {"SUSPENDED"},
    "REJECTED": {"UNDER_REVIEW"},
    "SUSPENDED": {"VERIFIED", "REJECTED"},
}


class AuthorizationType(StrEnum):
    """Regulatory authorizations – stored separately from identity verification."""
    DRUG_LICENCE_RETAIL = "DRUG_LICENCE_RETAIL"
    DRUG_LICENCE_WHOLESALE = "DRUG_LICENCE_WHOLESALE"
    HOSPITAL_PHARMACY_LICENCE = "HOSPITAL_PHARMACY_LICENCE"
    BIOMEDICAL_WASTE_AUTHORIZATION = "BIOMEDICAL_WASTE_AUTHORIZATION"
    MEDICINE_COLLECTION_AUTHORIZATION = "MEDICINE_COLLECTION_AUTHORIZATION"
    OTHER = "OTHER"


MEDICINE_HANDLING_AUTHORIZATIONS = {
    AuthorizationType.DRUG_LICENCE_RETAIL.value,
    AuthorizationType.DRUG_LICENCE_WHOLESALE.value,
    AuthorizationType.HOSPITAL_PHARMACY_LICENCE.value,
}
COLLECTION_AUTHORIZATIONS = {
    AuthorizationType.BIOMEDICAL_WASTE_AUTHORIZATION.value,
    AuthorizationType.MEDICINE_COLLECTION_AUTHORIZATION.value,
}


class AuthorizationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class DocumentType(StrEnum):
    REGISTRATION_CERTIFICATE = "REGISTRATION_CERTIFICATE"
    DRUG_LICENCE = "DRUG_LICENCE"
    PROFESSIONAL_CREDENTIAL = "PROFESSIONAL_CREDENTIAL"
    WASTE_AUTHORIZATION = "WASTE_AUTHORIZATION"
    PURCHASE_INVOICE = "PURCHASE_INVOICE"
    STORAGE_LOG = "STORAGE_LOG"
    BATCH_CERTIFICATE = "BATCH_CERTIFICATE"
    DISPOSAL_CERTIFICATE = "DISPOSAL_CERTIFICATE"
    OTHER = "OTHER"


# ---------------------------------------------------------------- medicines
class RestrictedClass(StrEnum):
    NONE = "NONE"
    CONTROLLED = "CONTROLLED"
    NARCOTIC = "NARCOTIC"
    PSYCHOTROPIC = "PSYCHOTROPIC"
    COLD_CHAIN = "COLD_CHAIN"
    OTHER_RESTRICTED = "OTHER_RESTRICTED"


EXCLUDED_FROM_PILOT = {c.value for c in RestrictedClass} - {RestrictedClass.NONE.value}


class DosageForm(StrEnum):
    TABLET = "TABLET"
    CAPSULE = "CAPSULE"
    SYRUP = "SYRUP"
    SUSPENSION = "SUSPENSION"
    OINTMENT = "OINTMENT"
    CREAM = "CREAM"
    DROPS = "DROPS"
    INHALER = "INHALER"
    POWDER = "POWDER"
    OTHER = "OTHER"


class PackagingCondition(StrEnum):
    SEALED_ORIGINAL = "SEALED_ORIGINAL"
    OPENED = "OPENED"
    DAMAGED = "DAMAGED"
    UNKNOWN = "UNKNOWN"


class Unit(StrEnum):
    TABLETS = "TABLETS"
    CAPSULES = "CAPSULES"
    STRIPS = "STRIPS"
    BOTTLES = "BOTTLES"
    TUBES = "TUBES"
    VIALS = "VIALS"
    PACKS = "PACKS"
    ML = "ML"
    G = "G"


class RegistrationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    COLLECTION_PENDING = "COLLECTION_PENDING"
    SCHEDULED = "SCHEDULED"
    COLLECTED = "COLLECTED"
    DISPOSAL_PENDING = "DISPOSAL_PENDING"
    DISPOSED = "DISPOSED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"


REGISTRATION_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"COLLECTION_PENDING", "CANCELLED", "ON_HOLD", "REJECTED"},
    "COLLECTION_PENDING": {"SCHEDULED", "SUBMITTED", "CANCELLED", "ON_HOLD", "REJECTED"},
    "SCHEDULED": {"COLLECTED", "SUBMITTED", "CANCELLED", "ON_HOLD"},
    "COLLECTED": {"DISPOSAL_PENDING", "ON_HOLD"},
    "DISPOSAL_PENDING": {"DISPOSED", "ON_HOLD"},
    "DISPOSED": {"CLOSED"},
    "ON_HOLD": {"SUBMITTED", "COLLECTION_PENDING", "SCHEDULED", "COLLECTED",
                "DISPOSAL_PENDING", "CANCELLED", "REJECTED"},
    "CLOSED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


class InventoryStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS = "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    ON_HOLD = "ON_HOLD"
    CLOSED = "CLOSED"


INVENTORY_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CLOSED"},
    "SUBMITTED": {"UNDER_REVIEW", "QUARANTINED", "DRAFT"},
    "UNDER_REVIEW": {"APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS", "REJECTED", "QUARANTINED", "ON_HOLD"},
    "ON_HOLD": {"UNDER_REVIEW", "REJECTED", "QUARANTINED"},
    "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS": {"QUARANTINED", "ON_HOLD", "CLOSED"},
    "QUARANTINED": {"CLOSED", "UNDER_REVIEW"},
    "REJECTED": {"CLOSED"},
    "CLOSED": set(),
}


class RecallStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    CHECKED_NO_RECALL = "CHECKED_NO_RECALL"
    RECALLED = "RECALLED"


class ReviewDecision(StrEnum):
    START_REVIEW = "START_REVIEW"
    APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS = "APPROVE_FOR_FURTHER_AUTHORIZED_PROCESS"
    REJECT = "REJECT"
    HOLD = "HOLD"
    QUARANTINE = "QUARANTINE"
    RESUME = "RESUME"


# ---------------------------------------------------------------- collection
class CollectionMethod(StrEnum):
    PICKUP = "PICKUP"
    DROP_OFF = "DROP_OFF"


class CollectionStatus(StrEnum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    COLLECTED = "COLLECTED"
    DISPOSAL_PENDING = "DISPOSAL_PENDING"
    DISPOSED = "DISPOSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


COLLECTION_TRANSITIONS = {
    "REQUESTED": {"ACCEPTED", "REJECTED", "CANCELLED"},
    "ACCEPTED": {"SCHEDULED", "CANCELLED"},
    "SCHEDULED": {"COLLECTED", "SCHEDULED", "CANCELLED"},
    "COLLECTED": {"DISPOSAL_PENDING"},
    "DISPOSAL_PENDING": {"DISPOSED"},
    "DISPOSED": {"CLOSED"},
    "REJECTED": set(),
    "CANCELLED": set(),
    "CLOSED": set(),
}
ACTIVE_COLLECTION_STATUSES = {"REQUESTED", "ACCEPTED", "SCHEDULED", "COLLECTED", "DISPOSAL_PENDING"}


class WasteCategory(StrEnum):
    EXPIRED_MEDICINES = "EXPIRED_MEDICINES"
    UNUSED_MEDICINES = "UNUSED_MEDICINES"
    LIQUID_MEDICINES = "LIQUID_MEDICINES"
    TOPICALS = "TOPICALS"
    INHALERS = "INHALERS"


class DisposalMethod(StrEnum):
    INCINERATION = "INCINERATION"
    AUTHORIZED_CBWTF = "AUTHORIZED_CBWTF"  # common bio-medical waste treatment facility
    RETURN_TO_MANUFACTURER = "RETURN_TO_MANUFACTURER"
    OTHER_AUTHORIZED = "OTHER_AUTHORIZED"


# ---------------------------------------------------------------- institutional
class InstitutionalRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class InterestStatus(StrEnum):
    INTEREST_EXPRESSED = "INTEREST_EXPRESSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    REVIEW_DECLINED = "REVIEW_DECLINED"
    WITHDRAWN = "WITHDRAWN"


class TransferStatus(StrEnum):
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TransferAuthorizationType(StrEnum):
    REGULATORY = "REGULATORY"
    QUALITY = "QUALITY"
    PROFESSIONAL = "PROFESSIONAL"


REQUIRED_TRANSFER_AUTHORIZATIONS = {
    TransferAuthorizationType.REGULATORY.value,
    TransferAuthorizationType.QUALITY.value,
    TransferAuthorizationType.PROFESSIONAL.value,
}

# ---------------------------------------------------------------- expiry risk
class ExpiryCategory(StrEnum):
    NORMAL = "NORMAL"
    MONITOR = "MONITOR"
    ATTENTION = "ATTENTION"
    URGENT = "URGENT"
    EXPIRED = "EXPIRED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


RISK_ALGORITHM_VERSION = "1.0.0"

DEFAULT_SETTINGS: dict[str, dict] = {
    "risk.weights": {"value": {"T": 0.50, "Q": 0.20, "D": 0.20, "C": 0.10},
                     "description": "Operational risk score weights (must sum to 1)."},
    "risk.quantity_reference": {"value": 1000,
                                "description": "Quantity at which quantity exposure reaches 100."},
    "risk.delay_reference_days": {"value": 30,
                                  "description": "Days without progress at which delay score reaches 100."},
    "risk.time_horizon_days": {"value": 365,
                               "description": "Days to expiry at or beyond which time score is 0."},
    "risk.level_thresholds": {"value": {"MODERATE": 30, "HIGH": 50, "CRITICAL": 75},
                              "description": "Lower bounds for risk levels."},
    "expiry.category_thresholds": {"value": {"URGENT": 30, "ATTENTION": 90, "MONITOR": 180},
                                   "description": "Upper bounds (days remaining) per expiry category."},
    "notifications.email_enabled": {"value": False, "description": "Send email notifications."},
    "integrations.maps_provider": {"value": "openstreetmap", "description": "openstreetmap | google"},
    "organizations.enabled_types": {
        "value": ["HOSPITAL", "PHARMACY", "HEALTHCARE_INSTITUTION", "NGO", "CHARITABLE_HEALTHCARE",
                  "COLLECTION_CENTRE"],
        "description": "Organization types that may register."},
}

KERALA_DISTRICTS = [
    "Alappuzha", "Ernakulam", "Idukki", "Kannur", "Kasaragod", "Kollam", "Kottayam",
    "Kozhikode", "Malappuram", "Palakkad", "Pathanamthitta", "Thiruvananthapuram",
    "Thrissur", "Wayanad",
]

ALLOWED_IMAGE_TYPES = {"png", "jpg", "jpeg", "webp"}
ALLOWED_DOCUMENT_TYPES = {"pdf", "png", "jpg", "jpeg"}

SAMPLE_PREFIX = "[SAMPLE] "
