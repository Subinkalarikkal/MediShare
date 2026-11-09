"""SQLAlchemy ORM models – 30 tables.

Conventions
-----------
* UUID primary keys (native UUID on PostgreSQL).
* All timestamps are stored as naive UTC (see utils.date_utils.utcnow).
* `deleted_at` implements soft deletion where appropriate.
* `is_sample` marks seeded demonstration records.
* Household records (medicine_registrations) and institutional records
  (inventory_batches) live in separate tables. Transfer tables only have
  foreign keys to inventory_batches, so a household registration can never be
  referenced by an institutional transfer.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from utils.date_utils import utcnow


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _fk(target: str, nullable: bool = False, ondelete: str | None = "RESTRICT",
        use_alter: bool = False) -> Mapped[Any]:
    return mapped_column(Uuid, ForeignKey(target, ondelete=ondelete, use_alter=use_alter), nullable=nullable,
                         index=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SampleMixin:
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# =========================================================== access control
class User(Base, TimestampMixin, SoftDeleteMixin, SampleMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(30), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    location: Mapped[Optional[str]] = mapped_column(String(300))
    district: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    pin_code: Mapped[Optional[str]] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    organization_id: Mapped[Optional[uuid.UUID]] = _fk("organizations.id", nullable=True, ondelete="SET NULL",
                                                       use_alter=True)
    # Professional credentials (reviewers / pharmacists)
    professional_registration_number: Mapped[Optional[str]] = mapped_column(String(100))
    professional_council: Mapped[Optional[str]] = mapped_column(String(200))
    professional_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    professional_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    password_hash: Mapped[Optional[str]] = mapped_column(String(300))  # Argon2id; unused with OIDC

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="UserRole.user_id"
    )
    organization: Mapped[Optional["Organization"]] = relationship(foreign_keys=[organization_id])


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id", ondelete="CASCADE")
    role_id: Mapped[uuid.UUID] = _fk("roles.id")
    assigned_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
    role: Mapped[Role] = relationship()


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)
    id: Mapped[uuid.UUID] = _pk()
    role_id: Mapped[uuid.UUID] = _fk("roles.id", ondelete="CASCADE")
    permission_id: Mapped[uuid.UUID] = _fk("permissions.id", ondelete="CASCADE")
    role: Mapped[Role] = relationship()
    permission: Mapped[Permission] = relationship()


class EmailLoginCode(Base):
    """One-time email verification codes (stored hashed, short-lived, attempt-limited)."""
    __tablename__ = "email_login_codes"
    __table_args__ = (Index("ix_email_code_email_created", "email", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="login", server_default="login")
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LoginEvent(Base):
    __tablename__ = "login_events"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(40))
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


# =========================================================== organizations
class Organization(Base, TimestampMixin, SoftDeleteMixin, SampleMixin):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("org_type", "registration_number", name="uq_org_type_regno"),
    )
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    org_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    registration_number: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    pin_code: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_person: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    website: Mapped[Optional[str]] = mapped_column(String(300))
    service_area: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    authorized_professional_name: Mapped[Optional[str]] = mapped_column(String(200))
    authorized_professional_registration: Mapped[Optional[str]] = mapped_column(String(100))
    # Identity verification – separate from regulatory authorizations
    verification_status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    verified_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")

    authorizations: Mapped[list["OrganizationAuthorization"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationDocument(Base):
    __tablename__ = "organization_documents"
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id", ondelete="CASCADE")
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    file_id: Mapped[uuid.UUID] = _fk("uploaded_files.id")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    file: Mapped["UploadedFile"] = relationship()


class OrganizationVerification(Base):
    """Immutable history of identity-verification decisions."""
    __tablename__ = "organization_verifications"
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id", ondelete="CASCADE")
    from_status: Mapped[Optional[str]] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OrganizationAuthorization(Base, TimestampMixin):
    """Regulatory authorizations (drug licences, waste-handling permits)."""
    __tablename__ = "organization_authorizations"
    __table_args__ = (
        CheckConstraint("valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
                        name="ck_auth_valid_dates"),
    )
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id", ondelete="CASCADE")
    authorization_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_number: Mapped[str] = mapped_column(String(100), nullable=False)
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(200))
    valid_from: Mapped[Optional[date]] = mapped_column(Date)
    valid_until: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    verified_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    document_file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    organization: Mapped[Organization] = relationship(back_populates="authorizations")


# =========================================================== medicines
class Medicine(Base, TimestampMixin, SampleMixin):
    """Medicine master. Contains identification data only – no clinical guidance."""
    __tablename__ = "medicines"
    __table_args__ = (
        UniqueConstraint("name", "manufacturer", "dosage_form", name="uq_medicine_identity"),
    )
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    generic_name: Mapped[Optional[str]] = mapped_column(String(250), index=True)
    active_ingredient: Mapped[Optional[str]] = mapped_column(String(250))
    manufacturer: Mapped[Optional[str]] = mapped_column(String(250))
    category: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    dosage_form: Mapped[str] = mapped_column(String(30), nullable=False, default="OTHER")
    packaging_type: Mapped[Optional[str]] = mapped_column(String(100))
    restricted_classification: Mapped[str] = mapped_column(String(30), default="NONE", nullable=False)


class MedicineRegistration(Base, TimestampMixin, SoftDeleteMixin, SampleMixin):
    """Household medicine registered for authorized collection and disposal ONLY."""
    __tablename__ = "medicine_registrations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_reg_quantity_positive"),
        CheckConstraint("manufacturing_date IS NULL OR manufacturing_date <= expiry_date",
                        name="ck_reg_dates"),
        Index("ix_reg_user_status", "user_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    medicine_id: Mapped[Optional[uuid.UUID]] = _fk("medicines.id", nullable=True, ondelete="SET NULL")
    medicine_name: Mapped[str] = mapped_column(String(250), nullable=False)
    generic_name: Mapped[Optional[str]] = mapped_column(String(250))
    manufacturer: Mapped[Optional[str]] = mapped_column(String(250))
    batch_number: Mapped[Optional[str]] = mapped_column(String(100))
    manufacturing_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    packaging_condition: Mapped[str] = mapped_column(String(30), nullable=False, default="UNKNOWN")
    storage_info: Mapped[Optional[str]] = mapped_column(Text)
    photo_file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    location: Mapped[Optional[str]] = mapped_column(String(300))
    district: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    pin_code: Mapped[Optional[str]] = mapped_column(String(10))
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False, index=True)
    ai_extraction_id: Mapped[Optional[uuid.UUID]] = _fk("ai_extraction_records.id", nullable=True, ondelete="SET NULL")
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class InventoryBatch(Base, TimestampMixin, SoftDeleteMixin, SampleMixin):
    """Institutional surplus inventory – verified organizations only."""
    __tablename__ = "inventory_batches"
    __table_args__ = (
        UniqueConstraint("organization_id", "medicine_id", "batch_number", name="uq_org_medicine_batch"),
        CheckConstraint("quantity > 0", name="ck_inv_quantity_positive"),
        CheckConstraint("manufacturing_date IS NULL OR manufacturing_date <= expiry_date",
                        name="ck_inv_dates"),
        Index("ix_inv_org_status", "organization_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    medicine_id: Mapped[uuid.UUID] = _fk("medicines.id")
    medicine_name: Mapped[str] = mapped_column(String(250), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturing_date: Mapped[Optional[date]] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    original_packaging_status: Mapped[str] = mapped_column(String(30), nullable=False, default="UNKNOWN")
    storage_conditions: Mapped[Optional[str]] = mapped_column(Text)
    storage_history: Mapped[Optional[str]] = mapped_column(Text)
    source_document_file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    storage_document_file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    recall_status: Mapped[str] = mapped_column(String(30), default="NOT_CHECKED", nullable=False)
    product_classification: Mapped[str] = mapped_column(String(30), default="NONE", nullable=False)
    regulatory_authorization_id: Mapped[Optional[uuid.UUID]] = _fk(
        "organization_authorizations.id", nullable=True, ondelete="SET NULL")
    reviewer_status: Mapped[Optional[str]] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(50), default="DRAFT", nullable=False, index=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    organization: Mapped[Organization] = relationship()
    medicine: Mapped[Medicine] = relationship()


# =========================================================== collection & disposal
class CollectionCentre(Base, TimestampMixin, SoftDeleteMixin, SampleMixin):
    __tablename__ = "collection_centres"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR (latitude BETWEEN -90 AND 90)", name="ck_centre_lat"),
        CheckConstraint("longitude IS NULL OR (longitude BETWEEN -180 AND 180)", name="ck_centre_lon"),
        CheckConstraint("daily_capacity >= 0", name="ck_centre_capacity"),
    )
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    district: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    pin_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320))
    operating_hours: Mapped[Optional[str]] = mapped_column(String(200))
    accepted_categories: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    collection_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    service_radius_km: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    daily_capacity: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="INACTIVE", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    organization: Mapped[Organization] = relationship()


class CollectionRequest(Base, TimestampMixin, SampleMixin):
    __tablename__ = "collection_requests"
    __table_args__ = (Index("ix_colreq_centre_status", "centre_id", "status"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    centre_id: Mapped[uuid.UUID] = _fk("collection_centres.id")
    request_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    preferred_date: Mapped[Optional[date]] = mapped_column(Date)
    collection_method: Mapped[str] = mapped_column(String(20), nullable=False)
    pickup_address: Mapped[Optional[str]] = mapped_column(Text)
    scheduled_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    actual_collection_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED", nullable=False, index=True)
    receipt_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    receipt_confirmed_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    disposal_status: Mapped[str] = mapped_column(String(30), default="NOT_STARTED", nullable=False)
    closure_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    items: Mapped[list["CollectionItem"]] = relationship(back_populates="request", cascade="all, delete-orphan")
    centre: Mapped[CollectionCentre] = relationship()
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("request_id", "registration_id", name="uq_request_registration"),)
    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = _fk("collection_requests.id", ondelete="CASCADE")
    # Household registrations only – never inventory batches.
    registration_id: Mapped[uuid.UUID] = _fk("medicine_registrations.id")
    quantity_received: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    condition_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    request: Mapped[CollectionRequest] = relationship(back_populates="items")
    registration: Mapped[MedicineRegistration] = relationship()


class CollectionEvent(Base):
    __tablename__ = "collection_events"
    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = _fk("collection_requests.id", ondelete="CASCADE")
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(30))
    to_status: Mapped[Optional[str]] = mapped_column(String(30))
    actor_id: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DisposalRecord(Base, SampleMixin):
    __tablename__ = "disposal_records"
    __table_args__ = (CheckConstraint("quantity_disposed > 0", name="ck_disposal_qty"),)
    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = _fk("collection_requests.id")
    centre_id: Mapped[uuid.UUID] = _fk("collection_centres.id")
    disposal_method: Mapped[str] = mapped_column(String(40), nullable=False)
    disposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_disposed: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    disposal_facility: Mapped[str] = mapped_column(String(250), nullable=False)
    reference_number: Mapped[str] = mapped_column(String(100), nullable=False)
    certificate_file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    recorded_by: Mapped[uuid.UUID] = _fk("users.id")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# =========================================================== institutional coordination
class InstitutionalRequest(Base, TimestampMixin, SampleMixin):
    __tablename__ = "institutional_requests"
    __table_args__ = (CheckConstraint("requested_quantity > 0", name="ck_instreq_qty"),)
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    required_category: Mapped[str] = mapped_column(String(100), nullable=False)
    medicine_id: Mapped[Optional[uuid.UUID]] = _fk("medicines.id", nullable=True, ondelete="SET NULL")
    medicine_name: Mapped[str] = mapped_column(String(250), nullable=False)
    requested_quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    required_documentation: Mapped[Optional[str]] = mapped_column(Text)
    request_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False, index=True)
    created_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    organization: Mapped[Organization] = relationship()


class InstitutionalRequestItem(Base, TimestampMixin):
    """An expression of interest linking a requirement to an approved inventory batch."""
    __tablename__ = "institutional_request_items"
    __table_args__ = (
        UniqueConstraint("institutional_request_id", "inventory_batch_id", name="uq_instreq_batch"),
        CheckConstraint("quantity > 0", name="ck_instreq_item_qty"),
    )
    id: Mapped[uuid.UUID] = _pk()
    institutional_request_id: Mapped[uuid.UUID] = _fk("institutional_requests.id", ondelete="CASCADE")
    inventory_batch_id: Mapped[uuid.UUID] = _fk("inventory_batches.id")
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="INTEREST_EXPRESSED", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    request: Mapped[InstitutionalRequest] = relationship()
    batch: Mapped[InventoryBatch] = relationship()


class ReviewRecord(Base):
    """Immutable reviewer decisions on institutional inventory."""
    __tablename__ = "review_records"
    id: Mapped[uuid.UUID] = _pk()
    inventory_batch_id: Mapped[uuid.UUID] = _fk("inventory_batches.id")
    institutional_request_item_id: Mapped[Optional[uuid.UUID]] = _fk(
        "institutional_request_items.id", nullable=True, ondelete="SET NULL")
    reviewer_id: Mapped[uuid.UUID] = _fk("users.id")
    decision: Mapped[str] = mapped_column(String(60), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(50))
    to_status: Mapped[Optional[str]] = mapped_column(String(50))
    checklist: Mapped[Optional[dict]] = mapped_column(JSON)
    authorization_reference: Mapped[Optional[str]] = mapped_column(String(150))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TransferRecord(Base, TimestampMixin):
    """Documentation of an institutional transfer. References inventory batches ONLY."""
    __tablename__ = "transfer_records"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_transfer_qty"),
        CheckConstraint("from_organization_id <> to_organization_id", name="ck_transfer_distinct_orgs"),
    )
    id: Mapped[uuid.UUID] = _pk()
    inventory_batch_id: Mapped[uuid.UUID] = _fk("inventory_batches.id")
    from_organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    to_organization_id: Mapped[uuid.UUID] = _fk("organizations.id")
    institutional_request_item_id: Mapped[Optional[uuid.UUID]] = _fk(
        "institutional_request_items.id", nullable=True, ondelete="SET NULL")
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_AUTHORIZATION", nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users.id")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    authorizations: Mapped[list["TransferAuthorization"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan")


class TransferAuthorization(Base):
    __tablename__ = "transfer_authorizations"
    __table_args__ = (UniqueConstraint("transfer_id", "authorization_type", name="uq_transfer_auth_type"),)
    id: Mapped[uuid.UUID] = _pk()
    transfer_id: Mapped[uuid.UUID] = _fk("transfer_records.id", ondelete="CASCADE")
    authorization_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_number: Mapped[str] = mapped_column(String(150), nullable=False)
    issuing_authority: Mapped[Optional[str]] = mapped_column(String(200))
    authorized_by: Mapped[uuid.UUID] = _fk("users.id")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    transfer: Mapped[TransferRecord] = relationship(back_populates="authorizations")


# =========================================================== AI & analytics
class ExpiryAlert(Base):
    __tablename__ = "expiry_alerts"
    __table_args__ = (Index("ix_alert_entity", "entity_type", "entity_id"),)
    id: Mapped[uuid.UUID] = _pk()
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[Optional[uuid.UUID]] = _fk("organizations.id", nullable=True, ondelete="CASCADE")
    user_id: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="CASCADE")
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    days_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="ck_risk_range"),
        Index("ix_risk_entity", "entity_type", "entity_id"),
    )
    id: Mapped[uuid.UUID] = _pk()
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[Optional[uuid.UUID]] = _fk("organizations.id", nullable=True, ondelete="CASCADE")
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    t_score: Mapped[float] = mapped_column(Float, nullable=False)
    q_score: Mapped[float] = mapped_column(Float, nullable=False)
    d_score: Mapped[float] = mapped_column(Float, nullable=False)
    c_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    expiry_category: Mapped[str] = mapped_column(String(20), nullable=False)
    days_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(20), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AIExtractionRecord(Base):
    __tablename__ = "ai_extraction_records"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    file_id: Mapped[Optional[uuid.UUID]] = _fk("uploaded_files.id", nullable=True, ondelete="SET NULL")
    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(80))
    raw_response: Mapped[Optional[str]] = mapped_column(Text)
    extracted: Mapped[Optional[dict]] = mapped_column(JSON)
    validation_errors: Mapped[Optional[list]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# =========================================================== platform operations
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_user_read", "user_id", "read_at"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id", ondelete="CASCADE")
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    channel: Mapped[str] = mapped_column(String(20), default="IN_APP", nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(30), default="DELIVERED", nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class UploadedFile(Base, SoftDeleteMixin):
    __tablename__ = "uploaded_files"
    __table_args__ = (CheckConstraint("size_bytes > 0", name="ck_file_size"),)
    id: Mapped[uuid.UUID] = _pk()
    owner_user_id: Mapped[uuid.UUID] = _fk("users.id")
    organization_id: Mapped[Optional[uuid.UUID]] = _fk("organizations.id", nullable=True, ondelete="SET NULL")
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AuditLog(Base):
    """Append-only audit trail. Services never update or delete rows here."""
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[uuid.UUID] = _pk()
    actor_user_id: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    before: Mapped[Optional[dict]] = mapped_column(JSON)
    after: Mapped[Optional[dict]] = mapped_column(JSON)
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[Optional[uuid.UUID]] = _fk("users.id", nullable=True, ondelete="SET NULL")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


ALL_TABLES = sorted(Base.metadata.tables.keys())
