# Security

## Authentication & authorization
* **OIDC (Google) via `st.login`** is the default (`AUTH_MODE=oidc`). The provider's verified email is mapped to
  a database user (`auth_service.resolve_login`); unverified provider emails are refused.
* Authentication ≠ authorization: a successful login grants nothing beyond the user's database roles; new
  institutional and reviewer accounts are `PENDING` (restricted).
* **Development login** (`AUTH_MODE=dev`) exists only for local demos with sample data, is labelled in the UI, and
  is refused whenever `APP_ENV=production`.
* **Server-side RBAC**: `st.session_state` stores only user id, email and login time. Every service call reloads
  status/roles/permissions from the database (`permission_service.require`). Forged in-memory roles are ignored
  (tested). Object-level checks enforce ownership (own registrations, own organization, own centre's requests).
* Super-admin-only permissions are protected from removal; grants that would break safety separations
  (e.g. individuals managing inventory) are refused.
* **Session expiry** after `SESSION_TIMEOUT_MINUTES` (default 60) and immediate logout if an account is suspended.
* **Argon2id** (`argon2-cffi`, t=3, m=64 MiB, p=4) for any locally managed password (none by default).
* First super admin is created only via `scripts/bootstrap_admin.py`, which refuses once one exists.

## Data protection
* SQLAlchemy ORM with bound parameters throughout (no string-built SQL) – SQL injection prevention.
* Inputs validated and cleaned (`utils/validators.py`): lengths, control characters, PIN, phone, email, dates,
  quantities, batch numbers, enumerations. HTML in user content is escaped before rendering.
* **Uploads**: size limit, extension allow-list **and** magic-byte sniffing, image decoding/verification,
  resolution limits, sanitised names, SHA-256 recorded. Files are stored privately (S3-compatible bucket with
  server-side encryption and short-lived presigned URLs; local private directory only in development). Every file
  read is permission-checked and audited.
* Sensitive data and AI: medicine photos are sent to Anthropic only with explicit per-upload consent and only if
  `AI_EXTERNAL_PROCESSING_ENABLED=true`; images are downscaled and EXIF is stripped. The analytics assistant
  receives only aggregated, de-identified metrics.
* Secrets come only from environment variables / Streamlit secrets; `.env` and `secrets.toml` are git-ignored;
  the integrations page shows configured/not-configured only.

## Auditability
Append-only `audit_logs` (actor, action, entity, before/after, details) for registrations, verification,
licences, reviews, transfers, disposal, settings, permission changes, file access, AI assistant queries and more;
plus `login_events`, `collection_events`, `organization_verifications` and `review_records`.

## Operational requirements (deployment)
HTTPS (provided by Streamlit Cloud / reverse proxy) · managed PostgreSQL with automated backups and
point-in-time recovery · private bucket with versioning · error monitoring (e.g. Sentry) · rate limiting at the
proxy / identity-provider level · data retention policy (proposal: closed household records anonymised after
3 years, audit logs retained 7 years – to be confirmed by the legal assessment).

## Known limitations
* Rate limiting is not implemented inside the app (Streamlit has no request middleware); use the hosting layer.
* No email provider is integrated yet; email deliveries are recorded as skipped.
* Regulatory compliance has **not** been assessed. MediShare must not be represented as authorized to collect,
  transfer or redistribute medicines merely because a workflow exists.
