"""
AI Hazard Decision Support Module
==================================

Replaces the previous locally-trained ML ensemble (LinearRegression +
RandomForestRegressor + SVR, see git history for ai/prediction.py) with a
call to a third-party AI API, per the capstone panel's recommendation to
use an AI API rather than developing and training a proprietary model.

Design notes
------------
The provider is a config switch, not a hardcoded choice. Three adapters are
implemented (Anthropic, OpenAI, Google Gemini) behind one interface, chosen
at runtime via the AI_PROVIDER environment variable. This exists so the
provider decision can be made empirically -- run the same inputs through
each adapter, compare quality/latency/cost, then lock in the winner for the
Chapter 3 write-up -- without touching call sites in app.py, scheduler.py,
or blueprints/ai.py.

Model ID strings for all three providers change often (new releases,
deprecations). The defaults below were current as of August 2026; verify
against each provider's docs before your defense / deployment:
  - Anthropic: https://docs.claude.com/en/docs/about-claude/models/overview
  - OpenAI:    https://platform.openai.com/docs/models
  - Gemini:    https://ai.google.dev/gemini-api/docs/pricing

Error handling
--------------
Per the panel's "API Response Validation" / "Error Handling" methodology
items: this module NEVER raises out to the caller for network/API/parsing
failures. It returns a degraded-but-safe response instead, so a flaky API
or an expired key doesn't take down the dashboard or the monitoring
scheduler. Only genuinely unexpected bugs propagate.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from models import Agency

# --- .env loading (same convention as services/realtime_data.py) ---------

def _load_dotenv():
    env_path = Path(__file__).resolve().parents[1] / '.env'
    if not env_path.exists():
        return
    with env_path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# --- Provider configuration ------------------------------------------------

AI_PROVIDER = os.getenv('AI_PROVIDER', 'anthropic').strip().lower()

PROVIDER_DEFAULTS = {
    'anthropic': {
        'api_key_env': 'ANTHROPIC_API_KEY',
        'model_env': 'ANTHROPIC_MODEL',
        'default_model': 'claude-sonnet-5',
    },
    'openai': {
        'api_key_env': 'OPENAI_API_KEY',
        'model_env': 'OPENAI_MODEL',
        'default_model': 'gpt-5.6-terra',
    },
    'gemini': {
        'api_key_env': 'GEMINI_API_KEY',
        'model_env': 'GEMINI_MODEL',
        'default_model': 'gemini-3.1-flash-lite',
    },
}

REQUEST_TIMEOUT_SECONDS = 15
VALID_LEVELS = {'Low', 'Moderate', 'High', 'Severe', 'Unknown', 'Insufficient Data', 'INSUFFICIENT_DATA'}
UNKNOWN_RISK_LEVELS = {'UNKNOWN', 'INSUFFICIENT_DATA', 'INSUFFICIENT DATA', 'INSUFFICIENT-DATA'}

SYSTEM_PROMPT = (
    "You are a disaster-risk decision-support assistant for a Digital Incident "
    "Command System covering the CALABARZON region of the Philippines. Given "
    "sensor and environmental readings for a single site, assess hazard risk "
    "and produce a structured, actionable recommendation for a duty officer.\n\n"
    "Respond with ONLY a JSON object -- no markdown fences, no commentary -- "
    "matching exactly this schema:\n"
    "{\n"
    '  "score": <number 0-100>,\n'
    '  "confidence": <number 0-100>,\n'
    '  "level": "Low" | "Moderate" | "High" | "Severe" | "Insufficient Data",\n'
    '  "message": "<one short paragraph, plain language, for a duty officer>",\n'
    '  "primary_factors": ["<factor>", ...],\n'
    '  "recommended_agencies": ["<agency>", ...],\n'
    '  "recommended_resources": ["<resource, with a rough quantity if useful>", ...]\n'
    "}\n\n"
    "Level bands: 0-24 Low, 25-49 Moderate, 50-74 High, 75-100 Severe. "
    "Use \"Insufficient Data\" when the hazard model cannot be assessed because a required input is unavailable. "
    "Only include agencies/resources genuinely warranted by the inputs given -- "
    "an empty list is correct when nothing is warranted. You provide a "
    "recommendation for a human to review, not a dispatch order; do not imply "
    "the recommendation has already been actioned."
)


def _build_user_prompt(hazard_type, rainfall_mm, river_level_m, humidity_pct,
                        population_density, earthquake_data=None, aftershock_forecast=None):
    lines = [
        f"Hazard type: {hazard_type}",
        f"Rainfall: {rainfall_mm} mm",
        f"River level: {river_level_m} m",
        f"Humidity: {humidity_pct}%",
        f"Population density (this grid square): {population_density}",
    ]
    if earthquake_data:
        top = earthquake_data[0] if isinstance(earthquake_data, list) and earthquake_data else None
        if top:
            lines.append(
                f"Most recent nearby seismic event: M{top.get('magnitude')} at "
                f"{top.get('place')}"
            )
    if aftershock_forecast:
        lines.append(f"Aftershock forecast context: {aftershock_forecast}")
    return "\n".join(lines)


# --- Provider adapters -------------------------------------------------
# Each adapter takes (system_prompt, user_prompt, api_key, model) and
# returns the raw text the model produced (expected to be JSON per the
# system prompt above). Adapters raise on transport failure; assess_hazard()
# is responsible for catching that and falling back gracefully.

def _call_anthropic(system_prompt, user_prompt, api_key, model):
    body = json.dumps({
        'model': model,
        'max_tokens': 700,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': user_prompt}],
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    return payload['content'][0]['text']


def _call_openai(system_prompt, user_prompt, api_key, model):
    body = json.dumps({
        'model': model,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    return payload['choices'][0]['message']['content']


def _call_gemini(system_prompt, user_prompt, api_key, model):
    body = json.dumps({
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': {'responseMimeType': 'application/json'},
    }).encode('utf-8')
    url = (
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        f'?key={api_key}'
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    return payload['candidates'][0]['content']['parts'][0]['text']


_ADAPTERS = {
    'anthropic': _call_anthropic,
    'openai': _call_openai,
    'gemini': _call_gemini,
}


# --- Response parsing / validation -----------------------------------------

def _strip_code_fences(text):
    text = text.strip()
    match = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
    return match.group(1) if match else text


def _level_from_score(score):
    if score < 25:
        return 'Low'
    if score < 50:
        return 'Moderate'
    if score < 75:
        return 'High'
    return 'Severe'


def _deterministic_low_risk_exit(hazard_type, rainfall_mm, river_level_m,
                                 humidity_pct, population_density,
                                 earthquake_data=None):
    low_rainfall = rainfall_mm is not None and rainfall_mm < 10
    low_river = river_level_m is None or river_level_m < 1.0
    low_humidity = humidity_pct is None or humidity_pct < 80
    low_population = population_density is None or population_density < 500
    low_seismic = True
    if earthquake_data and isinstance(earthquake_data, list) and earthquake_data:
        top = earthquake_data[0]
        magnitude = float(top.get('magnitude', 0) or 0)
        low_seismic = magnitude < 4.0

    if low_rainfall and low_river and low_humidity and low_population and low_seismic:
        factors = []
        if low_rainfall:
            factors.append('rainfall below 10 mm')
        if low_river:
            factors.append('river level below 1.0 m')
        if low_humidity:
            factors.append('humidity below 80%')
        if low_population:
            factors.append('population density below 500 people/km²')
        if low_seismic and earthquake_data:
            factors.append('seismic activity below M4.0')
        if not factors:
            factors.append('low hazard inputs')

        return {
            'type': hazard_type,
            'score': 10.0,
            'level': 'Low',
            'message': 'Deterministic input thresholds indicate low hazard risk; AI inference is not required.',
            'alert': False,
            'recommended_agencies': [],
            'recommended_resources': [],
            'confidence': 95.0,
            'primary_factors': factors,
            'degraded': False,
            'provider': 'deterministic',
            'model': 'threshold-filter-v1',
        }
    return None


def _normalize_recommended_agencies(recommended_agencies):
    if not isinstance(recommended_agencies, list):
        return []

    agency_lookup = None
    try:
        agency_lookup = {a.name.upper(): a.name for a in Agency.query.all()}
    except Exception:
        agency_lookup = None

    normalized = []
    for raw in recommended_agencies:
        if not isinstance(raw, str):
            continue
        agency_name = raw.strip()
        if not agency_name:
            continue
        if agency_lookup is None:
            normalized.append(agency_name)
            continue
        canonical = agency_lookup.get(agency_name.upper())
        if canonical:
            normalized.append(canonical)

    return list(dict.fromkeys(normalized))


def _parse_ai_response(raw_text, hazard_type):
    data = json.loads(_strip_code_fences(raw_text))

    score = float(data.get('score', 0))
    score = max(0.0, min(100.0, round(score, 1)))

    level = data.get('level')
    if level not in VALID_LEVELS:
        level = _level_from_score(score)

    raw_level = str(level or '').strip()
    if raw_level.upper() in UNKNOWN_RISK_LEVELS:
        level = 'INSUFFICIENT_DATA'

    message = str(data.get('message') or '').strip()
    if not message:
        message = f'{level} {hazard_type} risk assessed (score {score}).'

    confidence = data.get('confidence')
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = score
    confidence = max(0.0, min(100.0, round(confidence, 1)))

    primary_factors = data.get('primary_factors') or []
    if not isinstance(primary_factors, list):
        primary_factors = []
    else:
        primary_factors = [str(item).strip() for item in primary_factors if str(item).strip()]

    recommended_agencies = _normalize_recommended_agencies(data.get('recommended_agencies') or [])
    recommended_resources = data.get('recommended_resources') or []
    if not isinstance(recommended_resources, list):
        recommended_resources = []

    return {
        'type': hazard_type,
        'score': score,
        'level': level,
        'message': message,
        'alert': score >= 50,
        'confidence': confidence,
        'primary_factors': primary_factors,
        'recommended_agencies': recommended_agencies,
        'recommended_resources': recommended_resources,
        'degraded': False,
    }


def _fallback_response(hazard_type, reason):
    return {
        'type': hazard_type or 'unknown',
        'score': 0.0,
        'level': 'INSUFFICIENT_DATA',
        'message': (
            f'AI hazard assessment unavailable ({reason}). Falling back to manual '
            'review -- please assess this reading directly and retry shortly.'
        ),
        'alert': False,
        'recommended_agencies': [],
        'recommended_resources': [],
        'degraded': True,
        'provider': None,
        'model': None,
    }


# --- Public entry point ------------------------------------------------

def assess_hazard(hazard_type, rainfall_mm, river_level_m, humidity_pct,
                   population_density, earthquake_data=None, aftershock_forecast=None):
    hazard_type = hazard_type.strip().lower() if isinstance(hazard_type, str) else 'flood'

    cfg = PROVIDER_DEFAULTS.get(AI_PROVIDER)
    if cfg is None:
        return _fallback_response(hazard_type, f"unknown AI_PROVIDER '{AI_PROVIDER}'")

    deterministic_result = _deterministic_low_risk_exit(
        hazard_type, rainfall_mm, river_level_m, humidity_pct,
        population_density, earthquake_data,
    )
    if deterministic_result is not None:
        return deterministic_result

    api_key = os.getenv(cfg['api_key_env'])
    if not api_key:
        return _fallback_response(hazard_type, f"{cfg['api_key_env']} is not configured")

    model = os.getenv(cfg['model_env'], cfg['default_model'])
    user_prompt = _build_user_prompt(
        hazard_type, rainfall_mm, river_level_m, humidity_pct,
        population_density, earthquake_data, aftershock_forecast,
    )

    try:
        raw = _ADAPTERS[AI_PROVIDER](SYSTEM_PROMPT, user_prompt, api_key, model)
        result = _parse_ai_response(raw, hazard_type)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, KeyError, json.JSONDecodeError) as exc:
        return _fallback_response(hazard_type, str(exc))

    result['provider'] = AI_PROVIDER
    result['model'] = model
    return result


# Backwards-compatible alias: existing call sites do
# `from ai.prediction import predict_hazard`. Swapping that single import
# line to `from ai.decision_support import predict_hazard` is a drop-in
# replacement -- same keyword arguments, same return shape (with three new
# optional keys: recommended_agencies, recommended_resources, degraded).
def predict_hazard(hazard_type, rainfall_mm, river_level_m, humidity_pct,
                    population_density, earthquake_data=None, aftershock_forecast=None):
    return assess_hazard(
        hazard_type, rainfall_mm, river_level_m, humidity_pct,
        population_density, earthquake_data, aftershock_forecast,
    )
