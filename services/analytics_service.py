"""Database-backed metrics and reports. Every number here comes from a query.

Registered, collected (received) and disposed quantities are reported
separately – they are never mixed.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    CollectionCentre, CollectionItem, CollectionRequest, DisposalRecord, ExpiryAlert, InstitutionalRequest,
    InstitutionalRequestItem, InventoryBatch, LoginEvent, Medicine, MedicineRegistration, Organization,
    ReviewRecord, RiskAssessment, User,
)
from services import organization_service
from services.permission_service import Actor, require, require_active
from utils.constants import Perm
from utils.date_utils import month_key, today, utcnow


def _count(session: Session, stmt) -> int:
    return int(session.scalar(stmt) or 0)


# ---------------------------------------------------------------- public
def public_stats(session: Session) -> dict:
    return dict(
        verified_centres=_count(session, select(func.count(CollectionCentre.id)).where(
            CollectionCentre.verification_status == "VERIFIED", CollectionCentre.status == "ACTIVE")),
        verified_organizations=organization_service.count_verified(session),
        registrations=_count(session, select(func.count(MedicineRegistration.id)).where(
            MedicineRegistration.status != "DRAFT", MedicineRegistration.deleted_at.is_(None))),
        completed_collections=_count(session, select(func.count(CollectionRequest.id)).where(
            CollectionRequest.status.in_(["COLLECTED", "DISPOSAL_PENDING", "DISPOSED", "CLOSED"]))),
        disposal_records=_count(session, select(func.count(DisposalRecord.id))),
        districts_served=_count(session, select(func.count(func.distinct(CollectionCentre.district))).where(
            CollectionCentre.verification_status == "VERIFIED")),
    )


def quantities_by_unit(session: Session) -> pd.DataFrame:
    reg = session.execute(select(MedicineRegistration.unit, func.sum(MedicineRegistration.quantity))
                          .where(MedicineRegistration.status != "DRAFT", MedicineRegistration.deleted_at.is_(None))
                          .group_by(MedicineRegistration.unit)).all()
    col = session.execute(select(MedicineRegistration.unit, func.sum(CollectionItem.quantity_received))
                          .join(MedicineRegistration, MedicineRegistration.id == CollectionItem.registration_id)
                          .where(CollectionItem.quantity_received.is_not(None))
                          .group_by(MedicineRegistration.unit)).all()
    dis = session.execute(select(DisposalRecord.unit, func.sum(DisposalRecord.quantity_disposed))
                          .group_by(DisposalRecord.unit)).all()
    units = sorted({r[0] for r in reg} | {r[0] for r in col} | {r[0] for r in dis})
    m = lambda rows: {r[0]: float(r[1] or 0) for r in rows}  # noqa: E731
    r, c, d = m(reg), m(col), m(dis)
    return pd.DataFrame([dict(unit=u, registered=r.get(u, 0.0), collected_received=c.get(u, 0.0),
                              verified_disposed=d.get(u, 0.0)) for u in units])


# ---------------------------------------------------------------- admin KPIs
def admin_kpis(session: Session, actor: Actor) -> dict:
    require(session, actor, Perm.ANALYTICS_VIEW)
    thirty = utcnow() - timedelta(days=30)
    return dict(
        total_users=_count(session, select(func.count(User.id)).where(User.deleted_at.is_(None))),
        active_users=_count(session, select(func.count(func.distinct(LoginEvent.user_id))).where(
            LoginEvent.event_type == "LOGIN_SUCCESS", LoginEvent.created_at >= thirty)),
        verified_organizations=organization_service.count_verified(session),
        verified_centres=_count(session, select(func.count(CollectionCentre.id)).where(
            CollectionCentre.verification_status == "VERIFIED")),
        household_registrations=_count(session, select(func.count(MedicineRegistration.id)).where(
            MedicineRegistration.deleted_at.is_(None))),
        inventory_entries=_count(session, select(func.count(InventoryBatch.id)).where(InventoryBatch.deleted_at.is_(None))),
        pending_collection_requests=_count(session, select(func.count(CollectionRequest.id)).where(
            CollectionRequest.status.in_(["REQUESTED", "ACCEPTED", "SCHEDULED"]))),
        completed_collections=_count(session, select(func.count(CollectionRequest.id)).where(
            CollectionRequest.status.in_(["COLLECTED", "DISPOSAL_PENDING", "DISPOSED", "CLOSED"]))),
        expired_awaiting_disposal=_count(session, select(func.count(MedicineRegistration.id)).where(
            MedicineRegistration.expiry_date <= today(),
            MedicineRegistration.status.in_(["SUBMITTED", "COLLECTION_PENDING", "SCHEDULED", "COLLECTED",
                                             "DISPOSAL_PENDING", "ON_HOLD"]))) + _count(
            session, select(func.count(InventoryBatch.id)).where(InventoryBatch.status == "QUARANTINED")),
        pending_reviews=_count(session, select(func.count(InventoryBatch.id)).where(
            InventoryBatch.status.in_(["SUBMITTED", "UNDER_REVIEW", "ON_HOLD"]))),
        disposal_records=_count(session, select(func.count(DisposalRecord.id))),
        open_expiry_alerts=_count(session, select(func.count(ExpiryAlert.id)).where(ExpiryAlert.status == "OPEN")),
    )


def _monthly(dates) -> pd.DataFrame:
    s = pd.Series([month_key(d) for d in dates if d is not None], dtype="object")
    if s.empty:
        return pd.DataFrame(columns=["month", "count"])
    return s.value_counts().sort_index().rename_axis("month").reset_index(name="count")


def monthly_registrations(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    return _monthly(session.scalars(select(MedicineRegistration.created_at)
                                    .where(MedicineRegistration.deleted_at.is_(None))).all())


def monthly_collection_activity(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    req = _monthly(session.scalars(select(CollectionRequest.request_date)).all()).assign(kind="Requested")
    col = _monthly(session.scalars(select(CollectionRequest.actual_collection_date)
                                   .where(CollectionRequest.actual_collection_date.is_not(None))).all()).assign(kind="Collected")
    dis = _monthly(session.scalars(select(DisposalRecord.disposal_date)).all()).assign(kind="Disposed")
    return pd.concat([req, col, dis], ignore_index=True)


def district_registrations(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    rows = session.execute(select(func.coalesce(MedicineRegistration.district, "Unspecified"),
                                  func.count(MedicineRegistration.id))
                           .where(MedicineRegistration.deleted_at.is_(None))
                           .group_by(MedicineRegistration.district)).all()
    return pd.DataFrame(rows, columns=["district", "registrations"]).sort_values("registrations", ascending=False)


def inventory_category_distribution(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    rows = session.execute(select(func.coalesce(Medicine.category, "Uncategorised"), func.count(InventoryBatch.id))
                           .join(Medicine, Medicine.id == InventoryBatch.medicine_id)
                           .where(InventoryBatch.deleted_at.is_(None)).group_by(Medicine.category)).all()
    return pd.DataFrame(rows, columns=["category", "entries"])


def expiry_risk_distribution(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    sub = select(RiskAssessment.entity_id, func.max(RiskAssessment.created_at).label("mx"))\
        .group_by(RiskAssessment.entity_id).subquery()
    rows = session.execute(select(RiskAssessment.entity_type, RiskAssessment.risk_level, func.count(RiskAssessment.id))
                           .join(sub, (sub.c.entity_id == RiskAssessment.entity_id) & (sub.c.mx == RiskAssessment.created_at))
                           .group_by(RiskAssessment.entity_type, RiskAssessment.risk_level)).all()
    return pd.DataFrame(rows, columns=["entity_type", "risk_level", "count"])


def centre_performance(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    out = []
    for c in session.scalars(select(CollectionCentre).where(CollectionCentre.deleted_at.is_(None))):
        reqs = session.scalars(select(CollectionRequest).where(CollectionRequest.centre_id == c.id)).all()
        done = [r for r in reqs if r.actual_collection_date]
        avg = (sum((r.actual_collection_date - r.request_date).total_seconds() for r in done) / 86400 / len(done)
               if done else None)
        out.append(dict(centre=c.name, district=c.district, requests=len(reqs),
                        pending=sum(r.status in {"REQUESTED", "ACCEPTED", "SCHEDULED"} for r in reqs),
                        completed=len(done), rejected=sum(r.status == "REJECTED" for r in reqs),
                        avg_days_to_collect=round(avg, 1) if avg is not None else None))
    return pd.DataFrame(out)


def pending_vs_completed(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    rows = session.execute(select(CollectionRequest.status, func.count(CollectionRequest.id))
                           .group_by(CollectionRequest.status)).all()
    groups = {"Pending": {"REQUESTED", "ACCEPTED", "SCHEDULED"},
              "In disposal": {"COLLECTED", "DISPOSAL_PENDING"},
              "Completed": {"DISPOSED", "CLOSED"}, "Rejected / cancelled": {"REJECTED", "CANCELLED"}}
    agg = {g: sum(n for s, n in rows if s in members) for g, members in groups.items()}
    return pd.DataFrame([dict(group=k, count=v) for k, v in agg.items()])


def institutional_activity(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    frames = [
        _monthly(session.scalars(select(InventoryBatch.submitted_at).where(InventoryBatch.submitted_at.is_not(None))).all()).assign(kind="Inventory submitted"),
        _monthly(session.scalars(select(ReviewRecord.created_at)).all()).assign(kind="Review decisions"),
        _monthly(session.scalars(select(InstitutionalRequest.request_date)).all()).assign(kind="Institutional requests"),
    ]
    return pd.concat(frames, ignore_index=True)


def inventory_status_distribution(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    rows = session.execute(select(InventoryBatch.status, func.count(InventoryBatch.id))
                           .where(InventoryBatch.deleted_at.is_(None)).group_by(InventoryBatch.status)).all()
    return pd.DataFrame(rows, columns=["status", "count"])


def incomplete_documentation(session: Session, actor: Actor) -> pd.DataFrame:
    require(session, actor, Perm.ANALYTICS_VIEW)
    rows = []
    for o in session.scalars(select(Organization).where(Organization.deleted_at.is_(None))):
        gaps = organization_service.documentation_gaps(session, o)
        if gaps:
            rows.append(dict(organization=o.name, type=o.org_type, status=o.verification_status, gaps="; ".join(gaps)))
    for b in session.scalars(select(InventoryBatch).where(InventoryBatch.status.in_(["DRAFT", "ON_HOLD"]))):
        missing = [n for n, v in (("source documentation", b.source_document_file_id),
                                  ("storage documentation", b.storage_document_file_id)) if not v]
        if missing:
            rows.append(dict(organization=b.organization.name, type="INVENTORY", status=b.status,
                             gaps=f"{b.medicine_name} {b.batch_number}: missing " + ", ".join(missing)))
    return pd.DataFrame(rows)


def registration_dates(session: Session, actor: Actor) -> list:
    require(session, actor, Perm.ANALYTICS_VIEW)
    return [d.date() for d in session.scalars(select(MedicineRegistration.created_at)
                                              .where(MedicineRegistration.deleted_at.is_(None)))]


# ---------------------------------------------------------------- role dashboards
def individual_overview(session: Session, actor: Actor) -> dict:
    fresh = require(session, actor, Perm.MEDICINE_REGISTER)
    regs = session.scalars(select(MedicineRegistration).where(MedicineRegistration.user_id == fresh.user_id,
                                                              MedicineRegistration.deleted_at.is_(None))).all()
    reqs = session.scalars(select(CollectionRequest).where(CollectionRequest.user_id == fresh.user_id)).all()
    return dict(
        registrations=len(regs),
        awaiting_request=sum(r.status == "SUBMITTED" for r in regs),
        in_collection=sum(r.status in {"COLLECTION_PENDING", "SCHEDULED", "COLLECTED", "DISPOSAL_PENDING"} for r in regs),
        disposed=sum(r.status in {"DISPOSED", "CLOSED"} for r in regs),
        expired=sum(r.expiry_date <= today() and r.status not in {"DISPOSED", "CLOSED", "CANCELLED"} for r in regs),
        active_requests=sum(r.status in {"REQUESTED", "ACCEPTED", "SCHEDULED"} for r in reqs),
        upcoming=[r for r in reqs if r.status == "SCHEDULED"],
    )


def organization_overview(session: Session, actor: Actor) -> dict:
    fresh = require_active(session, actor, allow_pending=True)
    if fresh.organization_id is None:
        return {}
    batches = session.scalars(select(InventoryBatch).where(InventoryBatch.organization_id == fresh.organization_id,
                                                           InventoryBatch.deleted_at.is_(None))).all()
    by_status: dict = {}
    for b in batches:
        by_status[b.status] = by_status.get(b.status, 0) + 1
    active = [b for b in batches if b.status not in {"CLOSED", "REJECTED"}]
    return dict(
        total=len(batches), by_status=by_status,
        expiring_90=sum(0 < (b.expiry_date - today()).days <= 90 for b in active),
        expired=sum((b.expiry_date - today()).days <= 0 for b in active),
        requests=_count(session, select(func.count(InstitutionalRequest.id)).where(
            InstitutionalRequest.organization_id == fresh.organization_id)),
        interests=_count(session, select(func.count(InstitutionalRequestItem.id)).join(InstitutionalRequest).where(
            InstitutionalRequest.organization_id == fresh.organization_id)),
    )


def centre_overview(session: Session, actor: Actor) -> dict:
    fresh = require(session, actor, Perm.CENTRE_OPERATE, allow_pending=True)
    q = select(CollectionRequest).join(CollectionCentre, CollectionCentre.id == CollectionRequest.centre_id)\
        .where(CollectionCentre.organization_id == fresh.organization_id)
    reqs = session.scalars(q).all() if fresh.organization_id else []
    return dict(
        incoming=sum(r.status == "REQUESTED" for r in reqs),
        to_schedule=sum(r.status == "ACCEPTED" for r in reqs),
        scheduled=sum(r.status == "SCHEDULED" for r in reqs),
        awaiting_disposal=sum(r.status in {"COLLECTED", "DISPOSAL_PENDING"} for r in reqs),
        completed=sum(r.status in {"DISPOSED", "CLOSED"} for r in reqs),
        today=[r for r in reqs if r.status == "SCHEDULED" and r.scheduled_date and r.scheduled_date.date() == today()],
    )


def reviewer_overview(session: Session, actor: Actor) -> dict:
    fresh = require(session, actor, Perm.REVIEW_INVENTORY)
    return dict(
        submitted=_count(session, select(func.count(InventoryBatch.id)).where(InventoryBatch.status == "SUBMITTED")),
        under_review=_count(session, select(func.count(InventoryBatch.id)).where(InventoryBatch.status == "UNDER_REVIEW")),
        on_hold=_count(session, select(func.count(InventoryBatch.id)).where(InventoryBatch.status == "ON_HOLD")),
        interests=_count(session, select(func.count(InstitutionalRequestItem.id)).where(
            InstitutionalRequestItem.status.in_(["INTEREST_EXPRESSED", "UNDER_REVIEW"]))),
        my_decisions=_count(session, select(func.count(ReviewRecord.id)).where(ReviewRecord.reviewer_id == fresh.user_id)),
        verified=fresh.professional_verified,
    )


# ---------------------------------------------------------------- assistant snapshot
def assistant_snapshot(session: Session, actor: Actor) -> dict:
    """Aggregated, de-identified metrics for the AI assistant (no personal data)."""
    kpis = admin_kpis(session, actor)
    month_start = today().replace(day=1)
    this_month = dict(
        registrations=_count(session, select(func.count(MedicineRegistration.id)).where(
            MedicineRegistration.created_at >= month_start)),
        collection_requests=_count(session, select(func.count(CollectionRequest.id)).where(
            CollectionRequest.request_date >= month_start)),
        collections_completed=_count(session, select(func.count(CollectionRequest.id)).where(
            CollectionRequest.actual_collection_date >= month_start)),
        disposals=_count(session, select(func.count(DisposalRecord.id)).where(DisposalRecord.disposal_date >= month_start)),
    )
    attention = session.execute(select(InventoryBatch.medicine_name, InventoryBatch.batch_number, InventoryBatch.expiry_date,
                                       InventoryBatch.status, Organization.name)
                                .join(Organization, Organization.id == InventoryBatch.organization_id)
                                .where(InventoryBatch.status.notin_(["CLOSED", "REJECTED"]),
                                       InventoryBatch.expiry_date <= today() + timedelta(days=90))
                                .order_by(InventoryBatch.expiry_date).limit(25)).all()
    from services.collection_service import delayed_requests
    delayed = delayed_requests(session)
    return dict(
        as_of=today().isoformat(), kpis=kpis, this_month=this_month,
        district_registrations=district_registrations(session, actor).head(14).to_dict("records"),
        centre_performance=centre_performance(session, actor).to_dict("records"),
        delayed_requests=[dict(centre=r.centre.name, status=r.status, requested=r.request_date.date().isoformat())
                          for r in delayed],
        inventory_needing_attention=[dict(medicine=a[0], batch=a[1], expiry=a[2].isoformat(), status=a[3],
                                          organization=a[4], days_left=(a[2] - today()).days) for a in attention],
        incomplete_documentation=incomplete_documentation(session, actor).to_dict("records"),
        quantities=quantities_by_unit(session).to_dict("records"),
        pending_vs_completed=pending_vs_completed(session, actor).to_dict("records"),
    )
