# DICS-AI

DICS-AI is a Flask-based Digital Incident Command System for multi-hazard
incident management in CALABARZON. It combines citizen reports, incident
verification, response tasking, agency coordination, EOC dashboards, and
AI-assisted hazard prediction in a single web application.

The current runtime is organized around a role-oriented Flask blueprint layout,
SQLAlchemy models, a SQLite database, a schedulable monitoring job, and a
provider-backed AI decision-support layer.

---

## Architecture

The application is bootstrapped from [app.py](app.py):

- It creates the Flask app, configures the database connection, uploads, CSRF,
  rate limiting, and the scheduler.
- It registers the role blueprints for citizens, responders, coordinators,
  commanders, EOC staff, and the admin area.
- It exposes backward-compatible URL aliases that older templates and legacy
  route links still depend on.

The main runtime layers are:

- [models.py](models.py): the SQLAlchemy schema for users, incidents,
  incident responses, tasks, resources, citizen reports, geography, message
  records, and related domain objects.
- [scheduler.py](scheduler.py): hazard monitoring / ingestion loop that reads
  real-time weather and earthquake inputs and pushes a new incident record
  when a risk threshold is exceeded.
- [services/realtime_data.py](services/realtime_data.py): the live weather and
  earthquake adapter layer.
- [services/aftershock.py](services/aftershock.py): Omori-Utsu / Gutenberg-
  Richter aftershock probability logic and a calibrated proxy-region model.
- [ai/decision_support.py](ai/decision_support.py): provider adapter layer
  for AI risk scoring, with JSON parsing and degradation-safe fallback logic.
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

The AI prediction entry point is [ai/decision_support.py](ai/decision_support.py).
It loads provider defaults from environment variables and builds a JSON prompt
payload for the selected model.

Supported provider names:

- `anthropic`
- `openai`
- `gemini`

Required runtime environment variables for the provider layer:

| Variable | Required for | Notes |
|---|---|---|
| `AI_PROVIDER` | All AI prediction requests | Defaults to `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic adapter | Must be present when `AI_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | Anthropic adapter | Optional override; defaults to a current model string |
| `OPENAI_API_KEY` | OpenAI adapter | Must be present when `AI_PROVIDER=openai` |
| `OPENAI_MODEL` | OpenAI adapter | Optional override |
| `GEMINI_API_KEY` | Gemini adapter | Must be present when `AI_PROVIDER=gemini` |
| `GEMINI_MODEL` | Gemini adapter | Optional override |

The application also supports a local environment file (`.env`) at the project
root. The AI module loads values from that file before it looks at the process
environment.

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

The admin account is created only when the environment is configured with an
`ADMIN_PASSWORD`. During a local dev run you can still bootstrap the admin
record by setting the value before launching the app.

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
production-safety variables should be present:

```bash
export SECRET_KEY=development-secret
export DATABASE_URL=sqlite:////absolute/path/to/instance/database.db
export ADMIN_PASSWORD=change-me-now
```

For optional live AI execution, set the provider-specific keys as described
above.

### Start the app

```bash
python app.py
```

Then open:

http://127.0.0.1:5000

The app will initialize the SQLAlchemy schema and seed the canonical agency
and geography baseline as needed.

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

- [PRIVILEGE_MODEL.md](PRIVILEGE_MODEL.md) for the role model
- [data/README.md](data/README.md) for catalog and calibration notes

---

## Status

This repository is a current Flask app with a role-driven user experience,
calendar/scheduler-driven monitoring, AI provider adapters, services for
weather and earthquake ingestion, and a custom aftershock forecast engine with
proxy-region caveats.

For a production deployment, the AI provider keys, admin credentials, and
secret configuration should be reviewed and replaced outside of the repo.
