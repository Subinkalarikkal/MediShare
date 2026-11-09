# MediShare – Project Documentation

## 1. Purpose
MediShare coordinates the responsible management of unused and expired medicines in Kerala (pilot), with
configuration for later expansion. The specification (SRS/PRD v2.0 – Streamlit Edition) is the source of truth.

## 2. Architecture
A **modular monolith**:

```
Streamlit pages ──► components/session.act()/db() ──► services/*  ──► SQLAlchemy ──► PostgreSQL (SQLite in dev)
                                                     │  (authorization + validation + audit in every call)
                                                     └► ai/* (OCR, Claude, risk engine, assistant)
```

* **Business logic is independent of Streamlit.** Every service function has the form
  `fn(session, actor, ...)` and can be reused by a future REST API or mobile backend.
* **Authorization is server-side.** `Actor` objects only identify a user; `permission_service.require*()` reloads
  status, roles and permissions from the database on every call. `st.session_state` holds only the user id,
  email and login time.
* **Workflows are explicit state machines** (`utils/constants.py`), validated on every transition and recorded
  in the append-only `audit_logs` table (plus `collection_events`, `organization_verifications` and
  `review_records` histories).

## 3. Roles (7)
| Role | Main capabilities |
|---|---|
| Individual | Register household medicines, request collection, track to disposal |
| Institutional donor | Register organization, licences, documented surplus inventory, view reviews/transfers |
| NGO / healthcare org | Register requirements, see recommendations, express interest, view transfers |
| Collection centre | Register centres, accept/reject, schedule, confirm receipt, record disposal, reports |
| Reviewer / pharmacist | Review inventory and documents, decisions, interests, quality/professional authorizations |
| Administrator | Users, verification, monitoring, medicine master, risk, analytics, AI assistant, audit |
| Super administrator | + settings/thresholds, role permissions, administrators, integrations |

## 4. Safety rules enforced in code
| Rule | Where |
|---|---|
| No individual-to-individual transfer; no listing/claiming of household medicines | No such API exists; ownership checks in `medicine_service`, `collection_service` |
| Household records never enter institutional workflows | Separate tables; transfer/interest/review tables have no FK to `medicine_registrations`; explicit guard in `transfer_service.create_transfer` |
| Expired medicines never reused | `inventory_service.submission_blockers`, automatic quarantine (`quarantine_expired`), checks in review, matching and transfer |
| Opened/damaged packaging excluded from review | `submission_blockers` (sealed original packaging required) |
| Controlled / narcotic / psychotropic / cold-chain excluded | `EXCLUDED_FROM_PILOT` checks in registration, inventory, requests, matching, transfer |
| Verification ≠ authorization | `organizations.verification_status` vs `organization_authorizations` (type, validity, status) |
| Reviewer must be professionally verified; no self-review of own org | `require_verified_professional`, conflict check in `review_service` |
| Approval ≠ transfer | Transfers require REGULATORY + QUALITY + PROFESSIONAL authorizations before `AUTHORIZED` |
| AI cannot approve | `ai_generated=True` is rejected; assistant output filtered; AI has no write access |
| Audit trail | `utils/audit_logger.audit()` on every important action |

## 5. Modules (spec §4) → implementation
| # | Module | Implementation |
|---|---|---|
| 1 | Public website | `app_app_pages/01_Home.py`, `public_*.py`, `05_Collection_Centres.py`, `login.py` |
| 2 | Authentication | `app.py`, `services/auth_service.py`, `app_pages/login.py`, `onboarding.py` |
| 3 | User management | `services/user_service.py`, `app_pages/admin_users.py`, `14_Profile.py` |
| 4 | Organization management | `services/organization_service.py`, `07_Organization_Dashboard.py`, `admin_organizations.py` |
| 5 | Medicine master | `medicine_service` (master), `admin_medicines.py` |
| 6 | Household registration | `medicine_service`, `03_Medicine_Registration.py`, `04_My_Registrations.py` |
| 7 | Institutional inventory | `inventory_service`, `08_Organization_Inventory.py` |
| 8 | Collection centre directory | `collection_service` (centres), `05_Collection_Centres.py` (folium/OpenStreetMap) |
| 9 | Collection requests | `collection_service`, `disposal_service`, `06_Collection_Requests.py`, `10_Collection_Centre_Portal.py` |
| 10 | Institutional requests & matching | `institutional_request_service`, `review_service`, `transfer_service`, `09_…`, `11_…`, `admin_records.py` |
| 11 | AI extraction | `ai/ocr_service.py`, `ai/medicine_extraction.py` |
| 12 | Expiry-risk engine | `ai/expiry_risk_engine.py`, `admin_risk.py` |
| 13 | AI analytics assistant | `ai/analytics_assistant.py`, `13_Analytics.py` |
| 14 | Notifications | `services/notification_service.py`, `notifications.py`, `admin_notifications.py` |
| 15 | Admin analytics | `services/analytics_service.py`, `12_Admin_Dashboard.py`, `13_Analytics.py` |

## 6. Sample data
`scripts/seed_demo.py` creates demonstration data through the real services (all validation applies). Every
seeded row has `is_sample = true`; organization and centre names start with `[SAMPLE]`; users use the
`sample.medishare.in` domain; the UI shows a **SAMPLE DATA** tag. No dashboard figure is hard-coded.

## 7. Ambiguities and decisions (documented rather than invented)
* **Restricted household medicines**: the spec excludes them from the pilot, so registration is refused with
  guidance to contact a pharmacist or drug-control office, rather than accepted for disposal.
* **Capacity component C**: for household items it is active requests ÷ verified daily capacity in the district;
  for institutional batches it is review-queue size ÷ (verified reviewers × 15). Both are configurable later.
* **Who records which transfer authorization**: administrators record the regulatory reference; verified
  reviewers record quality and professional authorizations. Needs confirmation with the regulator.
* **Email delivery**: no provider is bundled. Email attempts are recorded as `SKIPPED_NO_PROVIDER` – the system
  never reports an email as sent unless a provider accepted it.
* **Organization types** are a fixed enum; super admins can enable/disable types via `organizations.enabled_types`.

## 8. Why the page folder is `app_pages/` (not `pages/`)
Streamlit automatically builds a sidebar menu from any folder named `pages/`, and shows it briefly while each
page loads – before MediShare's role-based `st.navigation` menu replaces it. That would reveal the names of
admin-only pages to anonymous visitors. Page files therefore live in `app_pages/`, and navigation is defined
only in `app.py` according to the signed-in user's roles.
