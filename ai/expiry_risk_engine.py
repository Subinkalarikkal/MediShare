"""Expiry-risk engine (operational prioritisation only – NOT a medicine-safety score).

Risk Score = wT·T + wQ·Q + wD·D + wC·C   (defaults 0.50 / 0.20 / 0.20 / 0.10)

All components are normalised to 0–100:
  T  time to expiry      – 100 when expired, falling linearly to 0 at the time horizon (365 d)
  Q  quantity exposure   – quantity / reference quantity, capped at 100
  D  processing delay    – days waiting without progress / reference days, capped at 100
  C  capacity pressure   – household: active requests vs. authorized centre capacity in the district
                           institutional: review-queue size vs. verified reviewer capacity
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    CollectionCentre, CollectionRequest, ExpiryAlert, InventoryBatch, MedicineRegistration, RiskAssessment,
    SystemSetting, User,
)
from services import notification_service as notif
from services.permission_service import Actor, require
from utils.audit_logger import audit
from utils.constants import DEFAULT_SETTINGS, RISK_ALGORITHM_VERSION, Perm
from utils.date_utils import today, utcnow

ACTIVE_HOUSEHOLD = ["SUBMITTED", "COLLECTION_PENDING", "SCHEDULED", "ON_HOLD"]
ACTIVE_INVENTORY = ["DRAFT", "SUBMITTED", "UNDER_REVIEW", "ON_HOLD", "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS",
                    "QUARANTINED"]
REVIEWS_PER_REVIEWER = 15


@dataclass
class RiskConfig:
    weights: dict
    quantity_reference: float
    delay_reference_days: float
    time_horizon_days: float
    level_thresholds: dict
    category_thresholds: dict

    @classmethod
    def defaults(cls) -> "RiskConfig":
        d = {k.split(".", 1)[1]: v["value"] for k, v in DEFAULT_SETTINGS.items()
             if k.startswith(("risk.", "expiry."))}
        return cls(weights=d["weights"], quantity_reference=d["quantity_reference"],
                   delay_reference_days=d["delay_reference_days"], time_horizon_days=d["time_horizon_days"],
                   level_thresholds=d["level_thresholds"], category_thresholds=d["category_thresholds"])

    @classmethod
    def from_db(cls, session: Session) -> "RiskConfig":
        cfg = cls.defaults()
        rows = {s.key: s.value for s in session.scalars(select(SystemSetting))}
        cfg.weights = rows.get("risk.weights", cfg.weights)
        cfg.quantity_reference = float(rows.get("risk.quantity_reference", cfg.quantity_reference))
        cfg.delay_reference_days = float(rows.get("risk.delay_reference_days", cfg.delay_reference_days))
        cfg.time_horizon_days = float(rows.get("risk.time_horizon_days", cfg.time_horizon_days))
        cfg.level_thresholds = rows.get("risk.level_thresholds", cfg.level_thresholds)
        cfg.category_thresholds = rows.get("expiry.category_thresholds", cfg.category_thresholds)
        return cfg


@dataclass
class RiskInputs:
    expiry_date: date
    quantity: float
    days_waiting: float
    capacity_pressure: float  # already 0–100
    reference_date: date | None = None


@dataclass
class RiskResult:
    days_remaining: int
    expiry_category: str
    t_score: float
    q_score: float
    d_score: float
    c_score: float
    risk_score: float
    risk_level: str
    algorithm_version: str = RISK_ALGORITHM_VERSION


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def expiry_category(days_remaining: int, thresholds: dict | None = None) -> str:
    t = thresholds or {"URGENT": 30, "ATTENTION": 90, "MONITOR": 180}
    if days_remaining <= 0:
        return "EXPIRED"
    if days_remaining <= t["URGENT"]:
        return "URGENT"
    if days_remaining <= t["ATTENTION"]:
        return "ATTENTION"
    if days_remaining <= t["MONITOR"]:
        return "MONITOR"
    return "NORMAL"


def time_score(days_remaining: int, horizon: float = 365) -> float:
    if days_remaining <= 0:
        return 100.0
    return _clamp(100.0 * (1 - days_remaining / horizon))


def quantity_score(quantity: float, reference: float = 1000) -> float:
    return _clamp(100.0 * max(quantity, 0) / max(reference, 1e-9))


def delay_score(days_waiting: float, reference: float = 30) -> float:
    return _clamp(100.0 * max(days_waiting, 0) / max(reference, 1e-9))


def risk_level(score: float, thresholds: dict | None = None) -> str:
    t = thresholds or {"MODERATE": 30, "HIGH": 50, "CRITICAL": 75}
    if score >= t["CRITICAL"]:
        return "CRITICAL"
    if score >= t["HIGH"]:
        return "HIGH"
    if score >= t["MODERATE"]:
        return "MODERATE"
    return "LOW"


def compute(inputs: RiskInputs, cfg: RiskConfig | None = None) -> RiskResult:
    cfg = cfg or RiskConfig.defaults()
    w = cfg.weights
    if abs(sum(w.values()) - 1.0) > 1e-6:
        raise ValueError("Risk weights must sum to 1.")
    days = (inputs.expiry_date - (inputs.reference_date or today())).days
    t = time_score(days, cfg.time_horizon_days)
    q = quantity_score(inputs.quantity, cfg.quantity_reference)
    d = delay_score(inputs.days_waiting, cfg.delay_reference_days)
    c = _clamp(inputs.capacity_pressure)
    score = round(_clamp(w["T"] * t + w["Q"] * q + w["D"] * d + w["C"] * c), 2)
    return RiskResult(days_remaining=days, expiry_category=expiry_category(days, cfg.category_thresholds),
                      t_score=round(t, 2), q_score=round(q, 2), d_score=round(d, 2), c_score=round(c, 2),
                      risk_score=score, risk_level=risk_level(score, cfg.level_thresholds))


# ---------------------------------------------------------------- capacity inputs
def district_capacity_pressure(session: Session, district: str | None) -> float:
    if not district:
        return 50.0
    capacity = session.scalar(select(func.coalesce(func.sum(CollectionCentre.daily_capacity), 0)).where(
        CollectionCentre.district == district, CollectionCentre.status == "ACTIVE",
        CollectionCentre.verification_status == "VERIFIED")) or 0
    if capacity <= 0:
        return 100.0
    pending = session.scalar(select(func.count(CollectionRequest.id))
                             .join(CollectionCentre, CollectionCentre.id == CollectionRequest.centre_id)
                             .where(CollectionCentre.district == district,
                                    CollectionRequest.status.in_(["REQUESTED", "ACCEPTED", "SCHEDULED"]))) or 0
    return _clamp(100.0 * pending / capacity)


def review_capacity_pressure(session: Session) -> float:
    reviewers = session.scalar(select(func.count(User.id)).where(User.professional_verified.is_(True),
                                                                 User.status == "ACTIVE")) or 0
    queue = session.scalar(select(func.count(InventoryBatch.id))
                           .where(InventoryBatch.status.in_(["SUBMITTED", "UNDER_REVIEW", "ON_HOLD"]))) or 0
    if reviewers == 0:
        return 100.0 if queue else 0.0
    return _clamp(100.0 * queue / (reviewers * REVIEWS_PER_REVIEWER))


# ---------------------------------------------------------------- persistence
def _store(session: Session, entity_type: str, entity_id: uuid.UUID, org_id, inputs: RiskInputs,
           res: RiskResult, cfg: RiskConfig) -> RiskAssessment:
    snap = {"expiry_date": inputs.expiry_date.isoformat(), "quantity": inputs.quantity,
            "days_waiting": inputs.days_waiting, "capacity_pressure": inputs.capacity_pressure,
            "reference_date": (inputs.reference_date or today()).isoformat(), "config": asdict(cfg)}
    ra = RiskAssessment(entity_type=entity_type, entity_id=entity_id, organization_id=org_id,
                        risk_score=res.risk_score, t_score=res.t_score, q_score=res.q_score, d_score=res.d_score,
                        c_score=res.c_score, risk_level=res.risk_level, expiry_category=res.expiry_category,
                        days_remaining=res.days_remaining, assessment_date=today(),
                        algorithm_version=res.algorithm_version, input_snapshot=snap)
    session.add(ra)
    return ra


def _alert(session: Session, entity_type: str, entity_id: uuid.UUID, category: str, days: int, *,
           user_id=None, org_id=None, label: str) -> bool:
    if category not in {"ATTENTION", "URGENT", "EXPIRED"}:
        return False
    exists = session.scalar(select(ExpiryAlert).where(ExpiryAlert.entity_type == entity_type,
                                                      ExpiryAlert.entity_id == entity_id,
                                                      ExpiryAlert.category == category,
                                                      ExpiryAlert.status == "OPEN"))
    if exists:
        exists.days_remaining = days
        return False
    session.add(ExpiryAlert(entity_type=entity_type, entity_id=entity_id, category=category, days_remaining=days,
                            user_id=user_id, organization_id=org_id))
    ntype = notif.INVENTORY_EXPIRED if category == "EXPIRED" else notif.EXPIRY_APPROACHING
    msg = (f"{label} has expired. It must go to authorized disposal." if category == "EXPIRED"
           else f"{label} expires in {days} day(s) ({category}).")
    if user_id:
        notif.notify(session, user_id, ntype, f"Expiry {category.lower()}", msg, entity_type.lower(), entity_id)
    if org_id:
        notif.notify_organization(session, org_id, ntype, f"Expiry {category.lower()}", msg, entity_type.lower(), entity_id)
    return True


def run_assessment(session: Session, actor: Actor) -> dict:
    """Assess all active household registrations and institutional batches."""
    fresh = require(session, actor, Perm.RISK_RUN)
    from services import inventory_service

    quarantined = inventory_service.quarantine_expired(session, fresh)
    cfg = RiskConfig.from_db(session)
    now = utcnow()
    assessed = alerts = 0
    seen: set[uuid.UUID] = set()
    district_cache: dict = {}
    for reg in session.scalars(select(MedicineRegistration).where(MedicineRegistration.status.in_(ACTIVE_HOUSEHOLD),
                                                                  MedicineRegistration.deleted_at.is_(None))):
        if reg.district not in district_cache:
            district_cache[reg.district] = district_capacity_pressure(session, reg.district)
        waiting = (now - (reg.submitted_at or reg.created_at)).days
        inputs = RiskInputs(reg.expiry_date, float(reg.quantity), waiting, district_cache[reg.district])
        res = compute(inputs, cfg)
        _store(session, "HOUSEHOLD_REGISTRATION", reg.id, None, inputs, res, cfg)
        seen.add(reg.id)
        assessed += 1
        alerts += _alert(session, "HOUSEHOLD_REGISTRATION", reg.id, res.expiry_category, res.days_remaining,
                         user_id=reg.user_id, label=f"'{reg.medicine_name}'")
    review_pressure = review_capacity_pressure(session)
    for b in session.scalars(select(InventoryBatch).where(InventoryBatch.status.in_(ACTIVE_INVENTORY),
                                                          InventoryBatch.deleted_at.is_(None))):
        waiting = (now - (b.submitted_at or b.created_at)).days if b.status != "APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS" else 0
        inputs = RiskInputs(b.expiry_date, float(b.quantity), waiting, review_pressure)
        res = compute(inputs, cfg)
        _store(session, "INVENTORY_BATCH", b.id, b.organization_id, inputs, res, cfg)
        seen.add(b.id)
        assessed += 1
        alerts += _alert(session, "INVENTORY_BATCH", b.id, res.expiry_category, res.days_remaining,
                         org_id=b.organization_id, label=f"{b.medicine_name} batch {b.batch_number}")
    resolved = 0
    for a in session.scalars(select(ExpiryAlert).where(ExpiryAlert.status == "OPEN")):
        if a.entity_id not in seen:
            a.status, a.resolved_at = "RESOLVED", now
            resolved += 1
    session.flush()
    summary = {"assessed": assessed, "new_alerts": alerts, "resolved_alerts": resolved,
               "quarantined_expired": quarantined, "algorithm_version": RISK_ALGORITHM_VERSION}
    audit(session, fresh.user_id, "RISK_ASSESSMENT_RUN", "risk_assessment", None, after=summary)
    return summary


def latest_assessments(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    latest_date = session.scalar(select(func.max(RiskAssessment.created_at)))
    if latest_date is None:
        return []
    sub = select(RiskAssessment.entity_id, func.max(RiskAssessment.created_at).label("mx"))\
        .group_by(RiskAssessment.entity_id).subquery()
    rows = session.scalars(select(RiskAssessment).join(sub, (sub.c.entity_id == RiskAssessment.entity_id)
                                                       & (sub.c.mx == RiskAssessment.created_at))).all()
    out = []
    for r in rows:
        if r.entity_type == "INVENTORY_BATCH":
            e = session.get(InventoryBatch, r.entity_id)
            label = f"{e.medicine_name} ({e.batch_number})" if e else "?"
        else:
            e = session.get(MedicineRegistration, r.entity_id)
            label = e.medicine_name if e else "?"
        out.append(dict(entity_type=r.entity_type, item=label, risk_score=r.risk_score, risk_level=r.risk_level,
                        expiry_category=r.expiry_category, days_remaining=r.days_remaining, T=r.t_score, Q=r.q_score,
                        D=r.d_score, C=r.c_score, assessed=r.created_at, version=r.algorithm_version))
    out.sort(key=lambda x: -x["risk_score"])
    return out


def org_assessments(session: Session, actor: Actor) -> list[dict]:
    fresh = require(session, actor, Perm.INVENTORY_MANAGE)
    rows = session.scalars(select(RiskAssessment).where(RiskAssessment.organization_id == fresh.organization_id)
                           .order_by(RiskAssessment.created_at.desc()).limit(500)).all()
    seen, out = set(), []
    for r in rows:
        if r.entity_id in seen:
            continue
        seen.add(r.entity_id)
        b = session.get(InventoryBatch, r.entity_id)
        out.append(dict(item=f"{b.medicine_name} ({b.batch_number})" if b else "?", risk_score=r.risk_score,
                        risk_level=r.risk_level, expiry_category=r.expiry_category, days_remaining=r.days_remaining,
                        assessed=r.created_at))
    return out


def open_alerts(session: Session, actor: Actor) -> list[dict]:
    require(session, actor, Perm.MONITOR_ALL)
    rows = session.scalars(select(ExpiryAlert).where(ExpiryAlert.status == "OPEN")
                           .order_by(ExpiryAlert.days_remaining)).all()
    return [dict(id=a.id, entity_type=a.entity_type, entity_id=str(a.entity_id), category=a.category,
                 days_remaining=a.days_remaining, created=a.created_at) for a in rows]


def acknowledge_alert(session: Session, actor: Actor, alert_id: uuid.UUID) -> None:
    fresh = require(session, actor, Perm.MONITOR_ALL)
    a = session.get(ExpiryAlert, alert_id)
    if a is None:
        from services.errors import NotFound
        raise NotFound("Alert not found.")
    a.status = "ACKNOWLEDGED"
    audit(session, fresh.user_id, "EXPIRY_ALERT_ACKNOWLEDGED", "expiry_alert", a.id)
