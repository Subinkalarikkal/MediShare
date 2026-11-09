# Database Schema

PostgreSQL (production) / SQLite (dev & tests). Defined in `database/models.py`; migrations in `alembic/versions/`.

**Conventions:** UUID primary keys (native `UUID` on PostgreSQL) · `created_at`/`updated_at` (naive UTC) ·
`deleted_at` soft delete on users, organizations, registrations, inventory, centres, files · `is_sample` on
demonstration data · CHECK constraints for quantities (> 0), date order (manufacturing ≤ expiry), coordinates,
risk range (0–100) · indexes on foreign keys, statuses and lookup columns.

## Tables (31)

### Authentication and access
| Table | Purpose / key columns |
|---|---|
| `users` | email (unique), username (unique, optional), name, phone, location, district, PIN, status (PENDING/ACTIVE/SUSPENDED/DEACTIVATED), organization_id, professional_registration_number/council/verified, last_login_at, password_hash (Argon2id, unused with OIDC) |
| `roles` | 7 role codes |
| `permissions` | permission codes (`utils/constants.Perm`) |
| `user_roles` | user ↔ role (unique pair), assigned_by |
| `role_permissions` | role ↔ permission (unique pair) |
| `email_login_codes` | one-time codes (purpose `login` or `reset`): email, salted SHA-256 hash, expiry, attempts, consumed_at |
| `login_events` | LOGIN_SUCCESS, LOGIN_UNREGISTERED, LOGIN_DENIED_*, LOGOUT, SESSION_EXPIRED |

### Organizations
| Table | Purpose |
|---|---|
| `organizations` | identity data + `verification_status` (PENDING → UNDER_REVIEW → VERIFIED / REJECTED / SUSPENDED); unique (org_type, registration_number) |
| `organization_documents` | typed documents → `uploaded_files` |
| `organization_verifications` | immutable history of verification decisions |
| `organization_authorizations` | **separate** regulatory authorizations: type, reference, authority, validity window, status |

### Medicine management
| Table | Purpose |
|---|---|
| `medicines` | medicine master (identification only), `restricted_classification`; unique (name, manufacturer, dosage_form) |
| `medicine_registrations` | **household** items; workflow DRAFT → … → CLOSED (+ REJECTED/CANCELLED/ON_HOLD) |
| `inventory_batches` | **institutional** items; unique (organization_id, medicine_id, batch_number); source/storage document FKs, recall status, classification, regulatory_authorization_id, status |

### Collection and disposal
| Table | Purpose |
|---|---|
| `collection_centres` | directory: coordinates, hours, accepted categories (JSON), availability, radius, capacity, status, verification |
| `collection_requests` | user, centre, dates, method, receipt confirmation, disposal status, closure |
| `collection_items` | request ↔ **household registration only** (unique pair), quantity received |
| `collection_events` | timeline of every request transition |
| `disposal_records` | method, date, quantity, unit, facility, manifest reference, certificate file, recorded_by |

### Institutional coordination
| Table | Purpose |
|---|---|
| `institutional_requests` | NGO requirements |
| `institutional_request_items` | expressions of interest (request ↔ inventory batch) |
| `review_records` | immutable reviewer decisions with checklist JSON and authorization reference |
| `transfer_records` | transfer documentation – FK to `inventory_batches` only; CHECK from_org ≠ to_org |
| `transfer_authorizations` | REGULATORY / QUALITY / PROFESSIONAL (unique per transfer & type) |

### AI and analytics
| Table | Purpose |
|---|---|
| `expiry_alerts` | ATTENTION / URGENT / EXPIRED alerts (OPEN/ACKNOWLEDGED/RESOLVED) |
| `risk_assessments` | score, T/Q/D/C components, level, category, days, date, algorithm version, input snapshot JSON |
| `ai_extraction_records` | OCR text, model, raw response, extracted JSON, validation errors, confirmation |

### Platform operations
| Table | Purpose |
|---|---|
| `notifications` | in-app / email channel rows with delivery status and read_at |
| `uploaded_files` | private storage metadata: backend, key, original name, content type, size, SHA-256, purpose |
| `audit_logs` | append-only: actor, action, entity, before/after JSON, details |
| `system_settings` | key → JSON value (risk weights/thresholds, expiry thresholds, enabled org types, email flag) |

## Workflow separation guarantee
No column in `transfer_records`, `transfer_authorizations`, `institutional_request_items` or `review_records`
references `medicine_registrations`, and `collection_items` references only `medicine_registrations`. This is
asserted by `tests/test_safety_acceptance.py::test_3_…`.

## Circular reference
`users.organization_id → organizations.id` and `organizations.created_by → users.id` form a cycle; the
migration adds `fk_users_organization_id` after both tables exist.
