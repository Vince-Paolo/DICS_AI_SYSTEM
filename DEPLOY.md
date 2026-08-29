# Deploying DICS AI System to Render + Neon (free tier)

## Why this combo
Render only allows one free-tier Postgres database per workspace, and
that slot is already used by another project. Rather than touching that
project or paying for anything, this setup splits the two pieces across
providers that are each genuinely free with no card required:
- App hosting: Render free web service
- Database: Neon free Postgres (separate provider, unlimited by Render's
  one-free-DB rule since it isn't a Render-managed resource)

## Neon free tier limits to know
- 0.5 GB storage, 100 compute-hours/month, permanent (not a trial)
- Scale-to-zero is mandatory on the free plan: the database sleeps after
  a few minutes of inactivity and cold-starts (~500ms) on the next query
- No credit card required

## Code changes made for this deploy (same as the earlier Render+Postgres attempt)
- `app.py`: fixed `postgres://` -> `postgresql://` scheme.
- `app.py`: legacy SQLite-only migration patches now only run when the
  DB is actually SQLite (`db.create_all()` covers Postgres already).
- `blueprints/admin.py`: SQLite-only "Export Backup" feature now shows a
  friendly message on Postgres instead of a broken export.
- `requirements.txt`: added `psycopg2-binary` and `gunicorn`.
- `render.yaml`: web service only (no `databases:` block this time --
  the database lives on Neon instead).

## Steps
1. Create a free Neon account at neon.tech (no card needed) and create a
   project. Copy the pooled connection string from the Neon console
   (Connection Details -- use "Pooled connection").
2. Push this repo to GitHub.
3. Render Dashboard -> New -> Blueprint -> connect your repo.
4. Render will prompt for `DATABASE_URL` (marked `sync: false` in
   render.yaml) -- paste your Neon connection string here.
5. Deploy. First request triggers `lazy_init()`, creating all tables via
   SQLAlchemy and seeding the default admin account + agencies.
6. Log in and change the default admin password immediately.

## Things to know
- Free web services on Render spin down after 15 min idle; free Neon DBs
  scale to zero after idle too. First request after a quiet period may
  be slow while both wake up.
- Uploaded citizen-report photos are NOT persisted (Render free has no
  disk) -- lost on redeploy. Same tradeoff as the earlier Render-only
  Postgres plan.
- Single gunicorn worker is intentional -- see comment in render.yaml.
