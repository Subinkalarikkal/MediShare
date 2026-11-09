from datetime import date, timedelta

import pytest

from ai import expiry_risk_engine as ere
from ai.forecasting import linear_forecast, monthly_counts
from services import medicine_service
from services.errors import PermissionDenied
from tests.conftest import reg_data


@pytest.mark.parametrize("days,cat", [(400, "NORMAL"), (181, "NORMAL"), (180, "MONITOR"), (91, "MONITOR"),
                                      (90, "ATTENTION"), (31, "ATTENTION"), (30, "URGENT"), (1, "URGENT"),
                                      (0, "EXPIRED"), (-10, "EXPIRED")])
def test_expiry_categories(days, cat):
    assert ere.expiry_category(days) == cat


@pytest.mark.parametrize("score,level", [(0, "LOW"), (29.99, "LOW"), (30, "MODERATE"), (49.9, "MODERATE"),
                                         (50, "HIGH"), (74.9, "HIGH"), (75, "CRITICAL"), (100, "CRITICAL")])
def test_risk_levels(score, level):
    assert ere.risk_level(score) == level


def test_formula_and_normalisation():
    ref = date(2026, 1, 1)
    r = ere.compute(ere.RiskInputs(ref + timedelta(days=0), quantity=2000, days_waiting=60, capacity_pressure=100,
                                   reference_date=ref))
    assert (r.t_score, r.q_score, r.d_score, r.c_score) == (100, 100, 100, 100)
    assert r.risk_score == 100 and r.risk_level == "CRITICAL" and r.expiry_category == "EXPIRED"
    r2 = ere.compute(ere.RiskInputs(ref + timedelta(days=365), 0, 0, 0, ref))
    assert r2.risk_score == 0 and r2.risk_level == "LOW"
    r3 = ere.compute(ere.RiskInputs(ref + timedelta(days=182), 500, 15, 40, ref))
    expected = 0.5 * (100 * (1 - 182 / 365)) + 0.2 * 50 + 0.2 * 50 + 0.1 * 40
    assert r3.risk_score == pytest.approx(expected, abs=0.02)


def test_invalid_weights_rejected():
    cfg = ere.RiskConfig.defaults()
    cfg.weights = {"T": 0.9, "Q": 0.2, "D": 0.2, "C": 0.1}
    with pytest.raises(ValueError):
        ere.compute(ere.RiskInputs(date.today(), 1, 1, 1), cfg)


def test_run_assessment_persists_and_alerts(session, admin, individual):
    medicine_service.create_registration(session, individual, **reg_data(expiry_date=date.today() + timedelta(days=10)))
    medicine_service.create_registration(session, individual, **reg_data(expiry_date=date.today() + timedelta(days=300)))
    summary = ere.run_assessment(session, admin)
    assert summary["assessed"] == 2 and summary["new_alerts"] == 1
    rows = ere.latest_assessments(session, admin)
    top = rows[0]
    assert top["expiry_category"] == "URGENT" and top["version"] == "1.0.0"
    from database.models import RiskAssessment
    ra = session.query(RiskAssessment).first()
    assert ra.input_snapshot["config"]["weights"]["T"] == 0.5
    # re-running does not duplicate open alerts
    assert ere.run_assessment(session, admin)["new_alerts"] == 0
    assert len(ere.open_alerts(session, admin)) == 1


def test_run_requires_permission(session, individual):
    with pytest.raises(PermissionDenied):
        ere.run_assessment(session, individual)


def test_forecast():
    dates = [date(2026, m, 5) for m in range(1, 7) for _ in range(m)]
    df = linear_forecast(monthly_counts(dates), periods=3)
    fc = df[df.kind == "forecast"]
    assert len(fc) == 3 and (fc.value >= 0).all() and fc.value.iloc[0] > 5
    assert linear_forecast(monthly_counts([])).empty
