# Deployment

## Public demo on Streamlit Community Cloud (sample data)
For showcasing the project without any external accounts:
1. share.streamlit.io → **Create app** → repository `MediShare`, branch `main`, main file `app.py`.
2. No secrets are needed: the committed `medishare_profile.toml` already selects demo mode. (Equivalent
   Secrets, if you prefer to set them explicitly:)
   ```toml
   APP_ENV = "demo"
   AUTH_MODE = "dev"
   DEMO_MODE = "true"
   ```
3. Deploy. On first start the app creates a SQLite database and loads the `[SAMPLE]` data; visitors sign in by
   choosing a sample account. Everything resets when the app restarts or sleeps.

Demo mode is for showcasing only: anyone can sign in as any sample account (including the super admin), and
the filesystem is temporary. Never enter real data. Demo mode is ignored when `APP_ENV="production"`.

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
