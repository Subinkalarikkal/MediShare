# MediShare

**Smart Medicine Waste Reduction & Authorized Donation Coordination Platform**
*Reducing Medicine Waste. Supporting Communities. Building a Healthier Future.*

MediShare is a modular Python / Streamlit application that helps households, healthcare institutions, NGOs and
authorized collection centres in Kerala manage unused and expired medicines responsibly. It has two strictly
separated workflows:

| Workflow | Who | What happens |
|---|---|---|
| **A – Household collection & disposal** | Individuals, collection centres | Register medicines → request pickup/drop-off at a verified centre → receipt → certified disposal → closure |
| **B – Institutional surplus coordination** | Verified hospitals/pharmacies, NGOs, pharmacists, admins | Licensed orgs submit documented surplus → professional review → matching recommendations → transfer *documentation* requiring regulatory, quality and professional authorization |

> ⚠️ MediShare is **not** a medicine marketplace and does **not** itself hold any regulatory authorization. A
> regulatory assessment under Indian drug law (Drugs and Cosmetics framework), bio-medical waste rules and data
> protection law is required before real-world operation. See [docs/SECURITY.md](docs/SECURITY.md).

## Quick start (local)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python scripts/seed_demo.py --reset   # creates medishare_dev.db with SAMPLE data
streamlit run app.py
```

With no `DATABASE_URL`, a local SQLite database is used. The included `.env` (git-ignored) sets
`AUTH_MODE=dev`, which shows a clearly labelled **development login** where you can pick any seeded
`@sample.medishare.in` account:

| Sample account | Role |
|---|---|
| `super.admin@…` / `admin@…` | Super administrator / Administrator |
| `pharmacist.reviewer@…` | Verified reviewer (pharmacist) · `reviewer.pending@…` unverified |
| `anjali@…`, `rahul@…`, `fathima@…` | Individuals |
| `hospital@…`, `pharmacy@…` | Institutional donors · `clinic@…` pending organization |
| `ngo@…` | NGO / healthcare organization |
| `centre@…` | Collection centre operator (3 centres) |

Development login is refused when `APP_ENV=production`; the default `AUTH_MODE` is `oidc` (Google).

**Optional:** set `ANTHROPIC_API_KEY` to enable Claude label reading and the AI analytics narrative. Without it,
OCR (Tesseract) and deterministic database answers are used, and the UI says so.

## Tests

```bash
python -m pytest            # 213 tests: services, safety acceptance, AI, migrations, Streamlit AppTest UI
```

## Project layout

```
app.py                 entry point: OIDC/dev auth, session expiry, role-based st.navigation
config.py              settings from env / st.secrets (no secrets in code)
app_pages/             Streamlit pages (public, individual, organization, centre, reviewer, admin)
components/            reusable UI: theme, session bridge, forms, tables, charts, dialogs, sidebar
services/              business logic + server-side authorization (Streamlit-independent)
ai/                    Claude client, OCR, label extraction, expiry-risk engine, forecasting, assistant
database/              SQLAlchemy models (30 tables), connection, seed, schemas, repositories
alembic/               migrations (PostgreSQL + SQLite)
utils/                 constants & state machines, validators, dates, images, location, audit logger
scripts/               seed_demo.py (sample data), bootstrap_admin.py (first super admin)
tests/                 pytest + Streamlit AppTest
docs/                  full documentation
```

## Documentation

- [Project documentation](docs/PROJECT_DOCUMENTATION.md) · [Implementation plan & status](docs/IMPLEMENTATION_PLAN.md)
- [Database schema](docs/DATABASE_SCHEMA.md) · [Service documentation](docs/API_AND_SERVICE_DOCUMENTATION.md)
- [User guide](docs/USER_GUIDE.md) · [AI documentation](docs/AI_DOCUMENTATION.md)
- [Security](docs/SECURITY.md) · [Deployment](docs/DEPLOYMENT.md)
