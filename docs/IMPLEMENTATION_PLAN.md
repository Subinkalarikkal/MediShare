# Implementation Plan & Status

All seven roadmap phases have been implemented and tested (status as of 2026-11-09).

| Phase | Deliverable | Status |
|---|---|---|
| 1 Foundation | Structure, config, PostgreSQL/SQLite connection, 30 SQLAlchemy models, Alembic migration, OIDC + dev auth, roles, navigation | ✅ |
| 2 Organization management | Registration, documents, verification workflow, licences, admin dashboard, role management | ✅ |
| 3 Medicine management | Medicine master, household registration, institutional inventory, image upload, expiry tracking | ✅ |
| 4 Collection & disposal | Centre directory + map, location search, requests, scheduling, receipt, disposal records, traceability | ✅ |
| 5 Institutional coordination | Requirements, inventory & document review, matching recommendations, review history, transfer authorization records | ✅ |
| 6 AI integration | OCR, Claude vision extraction, validation, manual confirmation, expiry-risk engine, forecasting, analytics assistant | ✅ |
| 7 Analytics & deployment | Dashboards, Plotly charts, reports/CSV, notifications, audit logs, tests, Docker, Streamlit deployment config | ✅ |

## Test results
`python -m pytest` → **250 passed** (Windows, Python 3.13.9, real Tesseract installed).

| File | Covers |
|---|---|
| `test_auth.py` | valid/invalid/unregistered/suspended login, pending status, role allow-list, session expiry, logout, Argon2id, bootstrap, dev-login never in production |
| `test_permissions.py` | admin data, forged roles, cross-org inventory, unrelated centre requests, reviewer vs settings, suspended org, safety-guarded grants |
| `test_users.py` | profile, search/filter, suspend/reactivate, admin protection, role grants, professional verification, notifications, contact form |
| `test_medicines.py` | valid registration, invalid dates/quantities/files, missing info, restricted products, drafts, privacy, master data |
| `test_inventory.py` | unverified org, missing licence/docs, expired batch, opened packs, restricted, duplicates, review/reject/quarantine, approval rules, unverified reviewer, unauthorized transfer |
| `test_collections.py` | requests, directory filtering, centre authorization, invalid centre, duplicates, scheduling, receipt, rejection, cancellation |
| `test_disposal.py` | disposal flow, closure, traceability, validation, centre ownership |
| `test_expiry_risk.py` | categories, levels, formula, weights, persistence, alerts, forecasting |
| `test_ai_extraction.py` | OCR/Claude extraction edge cases, failures, consent, assistant safety |
| `test_safety_acceptance.py` | the 9 critical safety acceptance tests + extra transfer/recall cases |
| `test_analytics.py` | KPIs from DB, quantity separation, documentation report, verification flow |
| `test_migrations.py` | Alembic upgrade/check/downgrade; PostgreSQL DDL rendering |
| `test_app.py` | Streamlit AppTest: every page for every role, login, session expiry, suspension, navigation isolation, UI workflows |

## Outstanding issues / next steps
1. **Regulatory & legal assessment** (Drugs and Cosmetics framework, bio-medical waste rules, DPDP Act) before any real operation.
2. Configure production OIDC, PostgreSQL (with backups), S3 bucket and an email provider; then run a full manual
   role-by-role acceptance test on the deployed URL.
3. Validate OCR/Claude extraction accuracy on a labelled set of real Indian medicine packs; calibrate confidence.
4. Confirm with regulators who may record each transfer authorization type.
5. Add Malayalam localisation, SMS notifications and real recall-database integration.
6. Hosting-level rate limiting and error monitoring.
7. The OIDC sign-in path is covered by code review and unit tests of `resolve_login`, but could not be exercised
   end-to-end locally without Google credentials.
