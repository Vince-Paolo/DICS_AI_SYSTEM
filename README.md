# DICS-AI

**Disaster Incident Coordination System** — an AI-assisted, multi-hazard
response platform for the CALABARZON region of the Philippines, built around
the real-world Incident Command System (ICS).

DICS-AI takes a hazard from citizen report through incident verification,
multi-agency response coordination, and post-incident evaluation — combining
six role-scoped dashboards, a machine-learning hazard predictor, a
literature-grounded aftershock forecasting engine, and live weather/earthquake
feeds into a single system.

> Capstone project.

---

## Features

- **Role-based ICS workflow** — six roles (citizen, field responder, agency
  coordinator, incident commander, EOC staff, admin), each with their own
  dashboard and a documented permission model (see
  [`PRIVILEGE_MODEL.md`](PRIVILEGE_MODEL.md)).
- **AI hazard prediction** — a 3-model ensemble (Linear Regression, Random
  Forest, SVR) scores flood/landslide risk 0–100 from rainfall, river level,
  soil moisture, and population density, with cross-validated RMSE tracked
  per hazard type.
- **Aftershock forecasting** — an Omori-Utsu + Gutenberg-Richter
  implementation estimates the probability of a qualifying aftershock within
  a given time window and radius of a mainshock, with region-specific
  parameters calibrated from a 135,281-event PHIVOLCS catalog (2016–2026).
- **Live hazard monitoring** — a background scheduler polls OpenWeatherMap
  and USGS earthquake data every 5 minutes and automatically opens an
  incident when a hazard crosses its alert threshold.
- **Citizen reporting** — photo upload, GPS coordinates, severity, and an
  anonymous option.
- **Multi-agency coordination** — task assignment, resource allocation,
  situation reports, and a response timeline across ten seeded agencies
  (BFP, PNP, DOH, DILG, MDRRMO, PAGASA, PHIVOLCS, Civil Defense, Red Cross,
  Local Government).
- **Analytics** — incident counts by hazard type, response-time
  distribution, and resource utilization, for admin and EOC staff.

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Flask (Blueprints — one per role) |
| Database / ORM | SQLite + Flask-SQLAlchemy |
| Machine learning | scikit-learn (Linear Regression, Random Forest, SVR) + joblib |
| Scientific modeling | Omori-Utsu + Gutenberg-Richter (custom) |
| Scheduling | Flask-APScheduler |
| Security | Flask-WTF (CSRF), Flask-Limiter, Werkzeug password hashing |
| Frontend | Server-rendered Jinja2 templates, vanilla CSS/JS |
| Testing / CI | pytest, standalone test scripts, GitHub Actions |

## Getting Started

### Requirements

- Python 3.11+ (developed against 3.14)
- pip

### Installation

```bash
git clone <this-repo-url>
cd DICS_AI_SYSTEM

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Environment variables

None are required to run the app locally — sensible defaults are used for
everything. Set these before deploying, or to enable optional features:

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Flask session signing key | random per-process key (sessions won't survive a restart — set this for real use) |
| `ADMIN_PASSWORD` | Password for the seeded `admin` account | `Admin123!` |
| `DATABASE_URL` | SQLAlchemy database URI | `sqlite:///instance/database.db` |
| `OPENWEATHER_API_KEY` | Enables live weather-driven hazard monitoring | unset — weather monitoring is skipped |
| `SESSION_COOKIE_SECURE` | Marks the session cookie `Secure` (set `true` behind HTTPS) | `false` |

A `.env` file in the project root is also supported and loaded automatically.

### Run

```bash
python app.py
```

Visit **http://127.0.0.1:5000**. On first run the app creates the SQLite
database, seeds the ten canonical agencies, and creates a default admin
account:

- **Username:** `admin`
- **Password:** value of `ADMIN_PASSWORD`, or `Admin123!` if unset

Change the admin password immediately in any real deployment.

### Run the tests

```bash
python test_ai_prediction.py
python test_aftershock.py
pytest tests/
```

`test_ai_prediction.py` and `test_aftershock.py` are standalone scripts (no
pytest dependency required) that exit non-zero on failure, so they also run
directly in CI — see `.github/workflows/aftershock-tests.yml`.

## Project Structure

```
DICS_AI_SYSTEM/
├── app.py                  Flask app factory, auth, dashboard routing,
│                           scheduler bootstrap, DB migrations
├── models.py                SQLAlchemy models (User, Incident,
│                           IncidentResponse, Task, Resource, ...)
├── scheduler.py              Background hazard/earthquake monitoring job
├── ai/
│   └── prediction.py        HazardPredictor — 3-model ML ensemble
├── services/
│   ├── aftershock.py        Omori-Utsu + Gutenberg-Richter forecasting
│   ├── realtime_data.py     Weather (OpenWeatherMap) + earthquake (USGS)
│   └── region_params.json   Calibrated per-region forecast parameters
├── blueprints/               One blueprint per role
│   ├── admin.py, ai.py, citizen.py, commander.py,
│   └── common.py, coordinator.py, eoc.py, responder.py
├── scripts/
│   └── calibrate_aftershock_regions.py   Fits region_params.json
│                                          from the PHIVOLCS catalog
├── data/                     hazard_training.csv, phivolcs_catalog.csv
├── templates/pages/          Role-specific Jinja2 templates
├── tests/, test_*.py         pytest + standalone test scripts
└── .github/workflows/        CI: aftershock tests + monthly recalibration
```

See [`PRIVILEGE_MODEL.md`](PRIVILEGE_MODEL.md) for the full role/permission
reference and [`data/README.md`](data/README.md) for details on the PHIVOLCS
catalog and how to refresh it.

## Roles

| Role | Real-world analogue | Scope |
|---|---|---|
| `citizen` | Member of the public | Only their own reports/alerts |
| `field_responder` | Fire/medical/rescue crew | Only tasks assigned to their own agency |
| `agency_coordinator` | Desk lead for one agency | Own agency's tasks/resources; read-only view of the wider response |
| `incident_commander` | ICS Incident Commander | Full control of incidents/responses assigned to them |
| `eoc_staff` | Emergency Operations Center watch officer | Read-only, org-wide view across all agencies and incidents |
| `admin` | System administrator | Accounts, configuration, backups — plus emergency override |

## Aftershock Forecasting

The forecasting engine estimates the probability of at least one qualifying
aftershock using a non-homogeneous Poisson process assumption:

```
P(≥1 event) = 1 − e^(−λ)

n(t) = K / (t + c)^p          Omori–Utsu aftershock rate
log₁₀ N(≥M) = a − b·M         Gutenberg–Richter magnitude distribution
```

Region-specific `K`, `c`, `p`, `a`, and `b` parameters are fit from real
historical sequences and stored in `services/region_params.json`. Where no
regional fit exists, the model falls back to global literature defaults
(Utsu, Ogata & Matsu'ura 1995) and flags every result with
`is_default_params` so that distinction is never lost downstream. A monthly
GitHub Actions workflow re-fits parameters against the tracked PHIVOLCS
catalog and opens a PR for human review — it does not auto-merge, since a
changed parameter set changes what the system tells stakeholders about
earthquake risk.

## Known Limitations

- `data/phivolcs_catalog.csv` has no automated refresh source and must be
  replaced manually to stay current — see `data/README.md`.
- Several permission-model policy questions are still open; see
  `PRIVILEGE_MODEL.md` §6.
- The hazard ML ensemble is trained on a single `hazard_training.csv`;
  more real historical data per region would likely improve accuracy.
- Aftershock calibration defaults to a Luzon-only bounding box.

## Deployment Link

- https://dics-ai-system.onrender.com/
