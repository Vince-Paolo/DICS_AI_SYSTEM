# DICS-AI

DICS-AI is a Flask-based Digital Incident Command System for multi-hazard
incident management in San Pablo City, Laguna. It combines citizen reports,
incident verification, response tasking, dispatch coordination, EOC dashboards,
and AI-assisted hazard prediction in a single web application.

Operational site predictions and automatically generated weather incidents are
scoped to San Pablo City. Broader regional weather, earthquake, flood, and
volcano feeds remain available as situational context; they do not create
out-of-city incidents.

The current runtime is organized around a role-oriented Flask blueprint layout,
SQLAlchemy models, a SQLite database, a schedulable monitoring job, and a
provider-backed hazard-analysis layer plus transparent dispatcher response
guidance.

---

## Architecture

The application is bootstrapped from [app.py](app.py):

- It creates the Flask app, configures the database connection, uploads, CSRF,
  rate limiting, and the scheduler.
- It registers the role blueprints for citizens, responders,
  commanders, EOC staff, and the admin area.
- It exposes backward-compatible URL aliases that older templates and legacy
  route links still depend on.

The main runtime layers are:

- [models.py](models.py): the SQLAlchemy schema for users, incidents,
  incident responses, tasks, resources, citizen reports, geography, message
  records, and related domain objects.
- [scheduler.py](scheduler.py): hazard monitoring loop that creates local
  incidents from San Pablo weather risk and dispatcher-only alerts for
  significant regional earthquakes, floods, and volcanic events near the city.
- [services/realtime_data.py](services/realtime_data.py): adapters for
  OpenWeather city observations, Open-Meteo San Pablo barangay rainfall, USGS
  earthquakes, GDACS floods and tropical cyclones, NASA EONET volcano events, and NASA FIRMS
  thermal hotspots. Rainfall and typhoon map layers return no events/data when
  their live feeds are unavailable; they do not substitute demo readings.
  Open-Meteo rainfall is shared-cache refreshed every 15 minutes (up to 80
  location-equivalent calls per refresh, or about 7,680/day at continuous
  maximum use). The free API is limited to non-commercial use and 10,000
  calls/day; confirm eligibility with Open-Meteo before production use by a
  government organization, since its published pricing page does not define
  government-use eligibility. The map links to Open-Meteo for required
  attribution. See [Open-Meteo pricing](https://open-meteo.com/en/pricing)
  and [licence](https://open-meteo.com/en/licence).
- Leaflet 1.9.4, MarkerCluster 1.5.3, and Leaflet.heat 0.2.0 are served from
  the local `static/vendor/leaflet` assets with their license notices included.
  This removes the map libraries' CDN dependency; live basemap tiles and
  external data feeds still require an internet connection.
- Chart.js 4.4.7 is served locally from `static/vendor/chartjs` with its MIT
  license; dashboard and analytics charts no longer depend on a CDN.
- [services/aftershock.py](services/aftershock.py): Omori-Utsu / Gutenberg-
  Richter aftershock probability logic and a calibrated proxy-region model.
- [ai/decision_support.py](ai/decision_support.py): provider adapter layer
  for AI risk scoring, with JSON parsing and degradation-safe fallback logic.
- [services/response_decision_support.py](services/response_decision_support.py):
  deterministic response-need scoring for open dispatcher emergencies and
  San Pablo scheduler-generated weather incidents. The dispatcher sees each
  factor and records the final choice; the score is guidance, not a probability
  or automatic dispatch. Ruleset `rules-v1` uses provisional
  thresholds pending CDRRMO validation. Vulnerable-person counts, geocoded
  unit locations, complete vehicle inventory, and mapped flood/landslide
  exposure are not currently available and are labeled as limitations.
  An outcome is withheld unless severity and location are identifiable;
  missing factors add no points and any dispatcher choice without a suggestion
  requires a reason.
- `/analytics` and `/api/analytics-data`: role-restricted operational summaries
  of incidents by year, month, hazard, barangay, and status; patient counts per
  emergency; response durations; and control-number search. Patient analytics
  expose aggregate counts only, not patient records.
- [templates/pages/](templates/pages/): role-driven Jinja templates for UI
  surfaces and operator workflows.

The current AI contract is not a hardwired ML ensemble. The prediction engine
is a provider adapter interface that reads `AI_PROVIDER` and delegates to the
active model adapter (`anthropic`, `openai`, or `gemini`). If the provider is
not configured or a provider call fails, the API returns a degraded payload
with an explicit `INSUFFICIENT_DATA` risk level rather than silently treating
that state as safe.

---

## AI Provider Setup

Remaining AI-assisted hazard analysis uses
[ai/decision_support.py](ai/decision_support.py). Manual dispatch guidance is
rules-based and does not call an AI provider; the legacy `/ai-prediction` URL
redirects to the dispatcher decision-support screen. Provider defaults are
loaded from environment variables for the AI-assisted hazard-analysis flows
that remain.

Supported provider names:

- `anthropic`
- `openai`
- `gemini`
- `ollama`

Required runtime environment variables for the provider layer:

| Variable | Required for | Notes |
|---|---|---|
| `AI_PROVIDER` | AI-assisted hazard analysis | Defaults to `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic adapter | Must be present when `AI_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | Anthropic adapter | Optional override; defaults to a current model string |
| `OPENAI_API_KEY` | OpenAI adapter | Must be present when `AI_PROVIDER=openai` |
| `OPENAI_MODEL` | OpenAI adapter | Optional override |
| `GEMINI_API_KEY` | Gemini adapter | Must be present when `AI_PROVIDER=gemini` |
| `GEMINI_MODEL` | Gemini adapter | Optional override |
| `OLLAMA_BASE_URL` | Ollama adapter | Optional; defaults to `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama adapter | Optional; defaults to `llama3.2:latest`; model must be pulled locally |
| `OLLAMA_TIMEOUT_SECONDS` | Ollama adapter | Optional; defaults to `120` for local model startup/inference |

Ollama runs locally and does not require an API key. Install Ollama, start its
service, pull the configured model (for example, `ollama pull llama3.2`), then
set `AI_PROVIDER=ollama`. Local Ollama availability is required wherever the
application server runs; a developer's local Ollama instance is not available
to a separately hosted deployment.

The application also supports a local environment file (`.env`) at the project
root. The AI module loads values from that file before it looks at the process
environment.

## Citizen Emergency Assistance (hotline page)

Citizens no longer type and submit incident reports. `/emergency-assistance`
is a public "call first" page: pick the kind of help (medical, police, fire and
rescue, disaster response) and tap **Call now**, which opens the phone's call
screen through a `tel:` link. There is no account, form or upload, and the
service worker keeps a copy so the page still opens without internet (the
phone call itself uses the cellular network).

Numbers come from environment variables (see `.env.example`); nothing is
hard-coded:

| Variable | Used for | Notes |
|---|---|---|
| `HOTLINE_GENERAL` | Main "Call Emergency" button | Defaults to `911` |
| `HOTLINE_MEDICAL`, `HOTLINE_POLICE`, `HOTLINE_FIRE` | Citizen Resources quick contacts | Optional; not repeated on Emergency Assistance, which uses the main emergency button |
| `HOTLINE_CDRRMO` | Disaster Response / CDRRMO contact | Shown on Emergency Assistance only when configured; admin/EOC users see a notice if unset |

Values that are not plausible phone numbers are ignored. Incidents now enter
the system through the automated monitoring feeds and staff/responder tools.
The legacy citizen report form and `/emergency-sos` are kept behind
`PUBLIC_REPORTING_ENABLED` (default `false`): `/citizen-report` redirects to
the hotline page and `/emergency-sos` returns `410 Gone`.

## Database Migrations

Production schema changes are managed with Flask-Migrate/Alembic. The
deployment command runs `flask --app app db upgrade` before Gunicorn starts,
using the configured `DATABASE_URL` (including PostgreSQL).

For a model change, generate and review a revision locally:

```bash
flask --app app db migrate -m "describe the schema change"
flask --app app db upgrade
```

Commit the generated file under `migrations/versions/` with the code change.
Do not use `db.create_all()` as a production migration mechanism. A database
that predates this Alembic history must be verified against the baseline schema
and stamped once with `flask --app app db stamp 46f03dfa7e95`; do this only
after confirming the existing database already contains the baseline tables
and constraints. New databases should use `db upgrade`, not `stamp`.

Example:

```bash
export AI_PROVIDER=anthropic
export ANTHROPIC_API_KEY=your-key-here
export ANTHROPIC_MODEL=claude-sonnet-5
export SECRET_KEY=local-dev-secret
export ADMIN_PASSWORD=change-me-now
```

If the configured provider is missing or the remote call fails, the runtime
falls back to a degraded, manual-review response contract that includes
`level: 'INSUFFICIENT_DATA'` and `degraded: True`.

---

## Database and Security

The app uses SQLite by default:

```bash
DATABASE_URL=sqlite:///instance/database.db
```

When the app starts, it initializes the database if it is not present. If a
live `SECRET_KEY` is not configured, the app emits a runtime warning and
creates a process-local random value. That is acceptable for a dev shell, but
not for production.

`ADMIN_PASSWORD` is required only until the initial bootstrap admin account is
created. It sets the initial password and forces an in-app change on first
login. Once the account exists, restarts never overwrite its password from the
environment; remove the variable after bootstrap. Use the account's password
reset/change flow to rotate an existing admin password.

---

## Setup and Run

### Prerequisites

- Python 3.11+ (current dev checks use Python 3.14 tooling in the workspace)
- `pip`
- A virtual environment

### Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On Linux/macOS the activation form is:

```bash
source .venv/bin/activate
```

### Configure environment

Use a `.env` file or export values before startup. At minimum, the common
production-safety variables should be present. `ADMIN_PASSWORD` is needed for
the first startup only, until the bootstrap admin is created:

```bash
export SECRET_KEY=development-secret
export DATABASE_URL=sqlite:////absolute/path/to/instance/database.db
export ADMIN_PASSWORD=change-me-now
# Set to true only when the deployment is served exclusively over HTTPS.
export ENABLE_HSTS=true
```

For optional live AI execution, set the provider-specific keys as described
above.

### Start the app

```bash
python app.py
```

Then open:

http://127.0.0.1:5000

The app will initialize the SQLAlchemy schema and seed the baseline geography
data as needed.

---

## Testing

The test suite and standalone scripts are split between route-level pytest
coverage and domain/regression checks:

```bash
.venv\Scripts\python.exe -m unittest tests.test_responder_routes -v
python test_aftershock.py
python test_ai_prediction.py
```

Or, when the environment has pytest installed:

```bash
pytest tests/
```

Every push and pull request runs the full unittest suite and the standalone
aftershock checks through the GitHub Actions workflow in
`.github/workflows/application-tests.yml`.

The app includes a responder-route regression suite in [tests/test_responder_routes.py](tests/test_responder_routes.py) and standalone prediction/aftershock checks.

---

## Project Layout

```
.
├── app.py
├── models.py
├── scheduler.py
├── blueprints/
├── ai/
├── services/
├── scripts/
├── data/
├── instance/
├── static/
├── templates/
├── tests/
└── requirements.txt
```

Additional references:

- [Permission matrix](docs/permissions-matrix.md) for the canonical role and capability policy
- [data/README.md](data/README.md) for catalog and calibration notes

---

## Status

This repository is a current Flask app with a role-driven user experience,
calendar/scheduler-driven monitoring, AI provider adapters, services for
weather and earthquake ingestion, and a custom aftershock forecast engine with
proxy-region caveats.

For a production deployment, the AI provider keys, admin credentials, and
secret configuration should be reviewed and replaced outside of the repo.
