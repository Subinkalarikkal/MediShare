# Deployment

## Hosted site (https://medishare.streamlit.app) – production with email verification
The committed `medishare_profile.toml` sets `APP_ENV="production"` and `AUTH_MODE="email"`: no sample data, no demo
login, and every sign-in / sign-up requires a 6-digit code emailed to the address. Database migrations run
automatically on startup.

Add these in Streamlit Cloud → your app → **⋮ → Settings → Secrets** (then Save; the app restarts):
```toml
# Email (required for sign-in). Gmail example – create an App Password at https://myaccount.google.com/apppasswords
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "587"
SMTP_USER = "your-address@gmail.com"
SMTP_PASSWORD = "your 16-character app password"
SMTP_FROM = "MediShare <your-address@gmail.com>"

# First super administrator (signs in with an emailed code like everyone else)
BOOTSTRAP_SUPER_ADMIN_EMAIL = "your-address@gmail.com"

# Permanent database (strongly recommended – without it data is lost when the app restarts)
DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```
**Built-in administrator.** `medishare_profile.toml` defines `ADMIN_USERNAME` (`medishare.admin`) and the
Argon2id hash of its password (`ADMIN_PASSWORD_HASH`); the password itself is not stored anywhere in the repository.
The account is created at start-up only if it doesn't exist and is never overwritten. To rotate the password,
generate a new hash (`python -c "from services.auth_service import hash_password; print(hash_password('NEW'))"`)
and replace `ADMIN_PASSWORD_HASH` (or set it in Streamlit Secrets, which take priority). The account's email is a
non-deliverable placeholder, so it cannot use email codes or Forgot password – it signs in with its password.

Without `DATABASE_URL` the app uses a SQLite file on Streamlit's temporary disk, which is wiped on every restart
or redeploy. A free PostgreSQL database is available from e.g. Neon (https://neon.tech).

The previous public demo can still be run locally (`APP_ENV=demo`, `AUTH_MODE=dev`, `DEMO_MODE=true`).

## Streamlit Community Cloud (prototype)
> For a real deployment, set `APP_ENV = "production"` in Secrets – this overrides `medishare_profile.toml`
> and disables the demo login and sample data.

1. Push the repository to GitHub (secrets are git-ignored).
2. New app → entry point `app.py`, Python 3.11+. `requirements.txt` (pinned, includes `Authlib`, which Google sign-in needs) and `packages.txt`
   (`tesseract-ocr`) are installed automatically.
3. **Managed PostgreSQL** (e.g. Neon, Supabase, RDS): create a database and user.
4. **Private object storage** (S3 or S3-compatible): private bucket, versioning on, an access key limited to it.
5. **Google OIDC**: Google Cloud Console → OAuth client (Web). Authorized redirect URI:
   `https://<your-app>.streamlit.app/oauth2callback`.
6. App settings → **Secrets**: paste `.streamlit/secrets.toml.example` filled in (`APP_ENV="production"`,
   `AUTH_MODE="oidc"`, `DATABASE_URL`, storage keys, `ANTHROPIC_API_KEY`, `[auth]` and `[auth.google]`).
7. Run migrations and bootstrap the first super administrator from a trusted machine:
   ```bash
   export DATABASE_URL=postgresql+psycopg2://...
   alembic upgrade head
   python scripts/bootstrap_admin.py --email you@your-org.in --name "Your Name"
   ```
8. Sign in with that Google account, verify organizations/centres/reviewers, and test every role workflow.
9. Configure database backups (PITR), monitoring and alerting.

Never use the Streamlit Cloud filesystem for permanent storage, and never put credentials in source code.
Do **not** run `scripts/seed_demo.py` against production.

## Docker / private hosting (production consideration)
```bash
docker build -t medishare .
docker run -p 8501:8501 --env-file .env.production medishare   # runs `alembic upgrade head` then the app
```
Place behind an HTTPS reverse proxy with rate limiting; use a managed PostgreSQL and private bucket.

## Environment variables
See `.env.example`: `APP_ENV`, `DATABASE_URL`, `AUTH_MODE`, `OIDC_PROVIDER`, `SESSION_TIMEOUT_MINUTES`,
`ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `AI_EXTERNAL_PROCESSING_ENABLED`, `GOOGLE_MAPS_API_KEY`, `STORAGE_*`,
`EMAIL_PROVIDER_API_KEY`, `MAX_UPLOAD_MB`, `TESSERACT_CMD`, `BOOTSTRAP_SUPER_ADMIN_EMAIL`.

## Local development
```bash
pip install -r requirements.txt
python scripts/seed_demo.py --reset
streamlit run app.py          # .env sets AUTH_MODE=dev for the demo login
python -m pytest
```
