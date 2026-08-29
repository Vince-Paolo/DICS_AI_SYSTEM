import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from models import utcnow

# OpenWeatherMap API key must be provided via environment variable.
# Example: set OPENWEATHER_API_KEY=your_real_key before running the app.
# This module also supports a local .env file in the project root.

# Simple in-memory cache for API responses (reduces duplicate calls)
_cache = {
    'weather': {},
    'earthquakes': {'data': None, 'timestamp': None},
    'flood_events': {'data': None, 'timestamp': None},
    'volcano_events': {'data': None, 'timestamp': None},
}
_cache_duration = 300  # 5 minutes
CALABARZON_CITIES = {
    'lipa': 'Lipa',
    'batangas': 'Batangas',
    'tanauan': 'Tanauan',
    'calamba': 'Calamba',
    'san pablo': 'San Pablo',
    'lucena': 'Lucena',
    'tagaytay': 'Tagaytay',
    'imus': 'Imus',
    'dasmariñas': 'Dasmariñas',
    'cavite': 'Cavite',
    'taytay': 'Taytay',
    'antipolo': 'Antipolo',
    'quezon': 'Quezon',
    'rizal': 'Rizal',
    'carmona': 'Carmona',
    'alaminos': 'Alaminos',
    'nagcarlan': 'Nagcarlan',
    'san fernando': 'San Fernando',
}


def _canonical_city_key(city):
    if not city:
        return None
    normalized = city.strip().lower().replace('ñ', 'n')
    for key in CALABARZON_CITIES:
        if key.replace('ñ', 'n') == normalized:
            return key
    return None


def get_all_weather_data():
    """Fetch current weather for every Calabarzon city in the supported list."""
    return {
        display_name: get_weather_data(city_key)
        for city_key, display_name in CALABARZON_CITIES.items()
    }

# Approximate Calabarzon bounding box (Luzon, Philippines)
CALABARZON_BBOX = {
    'minlatitude': 13.1,
    'maxlatitude': 14.4,
    'minlongitude': 120.4,
    'maxlongitude': 122.0,
}


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


def _get_openweather_api_key():
    key = os.getenv("OPENWEATHER_API_KEY")
    if key and key != "YOUR_OPENWEATHER_API_KEY":
        return key
    return None


def _fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            if resp.getcode() != 200:
                return None
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode('utf-8'))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, TimeoutError):
        return None


def get_weather_data(city="Lipa"):
    """Fetch current weather for a Calabarzon city.
    Only returns data for Calabarzon locations.
    Uses in-memory cache to reduce API calls.
    """
    canonical_city = _canonical_city_key(city)
    if not canonical_city:
        return None

    # Check cache first
    cached = _cache['weather'].get(canonical_city)
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    api_key = _get_openweather_api_key()
    if not api_key:
        return None

    params = {
        'q': f"{canonical_city},PH",
        'appid': api_key,
        'units': 'metric',
    }
    url = f"https://api.openweathermap.org/data/2.5/weather?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url)
    if not data:
        return None

    rainfall = 0
    if 'rain' in data:
        rainfall = data['rain'].get('1h', 0) or 0

    result = {
        'city': CALABARZON_CITIES.get(canonical_city, canonical_city.title()),
        'temperature': data.get('main', {}).get('temp'),
        'humidity': data.get('main', {}).get('humidity'),
        'pressure': data.get('main', {}).get('pressure'),
        'wind_speed': data.get('wind', {}).get('speed'),
        'rainfall': rainfall,
        'weather': data.get('weather', [{}])[0].get('description'),
        'fetched_at': utcnow().isoformat() + 'Z'
    }
    # Cache the result by city
    _cache['weather'][canonical_city] = {'data': result, 'timestamp': utcnow()}
    return result


def get_earthquake_data():
    """Fetch recent earthquake events from the Calabarzon region.
    Uses in-memory cache to reduce API calls.
    """
    # Check cache first
    cached = _cache.get('earthquakes')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    # starttime bounds the feed to genuinely recent activity. Without this,
    # USGS's "10 most recent events in this bounding box" can mean the same
    # single earthquake from weeks ago, indefinitely, if CALABARZON simply
    # hasn't had 10 newer quakes since -- which is common for this region.
    # Incident-level de-duplication in scheduler.py also keys off each
    # quake's own USGS event id (not just this time bound), so the two
    # protections are independent: this keeps the "recent activity" feed
    # honest, that keeps duplicate Incident rows from ever being created.
    start_date = (utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=geojson&minlatitude={CALABARZON_BBOX['minlatitude']}"
        f"&maxlatitude={CALABARZON_BBOX['maxlatitude']}"
        f"&minlongitude={CALABARZON_BBOX['minlongitude']}"
        f"&maxlongitude={CALABARZON_BBOX['maxlongitude']}"
        f"&starttime={start_date}"
        "&orderby=time&limit=10"
    )
    data = _fetch_json(url)
    if not data:
        return []

    earthquakes = []
    for feat in data.get('features', []):
        prop = feat.get('properties', {})
        earthquakes.append({
            'event_id': feat.get('id'),
            'magnitude': prop.get('mag'),
            'place': prop.get('place'),
            'time': prop.get('time')
        })
    # Cache the result
    _cache['earthquakes'] = {'data': earthquakes, 'timestamp': utcnow()}
    return earthquakes


def get_flood_events():
    """Fetch recent flood events affecting the Philippines from GDACS
    (Global Disaster Alert and Coordination System -- UN OCHA / EC Joint
    Research Centre, https://www.gdacs.org). No API key required.

    GDACS is a global feed covering all hazard types; EVENTS4APP returns the
    ~100 most recent events worldwide from the last few days, so we filter
    client-side for eventtype == 'FL' (flood) and a country field containing
    "Philippines" -- GDACS does not expose a server-side country filter on
    this endpoint. Field names (eventtype, alertlevel, severitydata, etc.)
    follow GDACS's documented GeoJSON schema; see
    https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf.
    Uses in-memory cache to reduce API calls.
    """
    cached = _cache.get('flood_events')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    url = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
    data = _fetch_json(url)
    if not data:
        return []

    floods = []
    for feat in data.get('features', []) or []:
        prop = feat.get('properties', {}) or {}
        if (prop.get('eventtype') or '').strip().upper() != 'FL':
            continue
        country = (prop.get('country') or '').strip()
        if 'philippines' not in country.lower():
            continue

        lat = lon = None
        geometry = feat.get('geometry') or {}
        if geometry.get('type') == 'Point':
            coords = geometry.get('coordinates') or []
            if len(coords) >= 2:
                lon, lat = coords[0], coords[1]

        severity = prop.get('severitydata') or {}
        floods.append({
            'event_id': prop.get('eventid'),
            'name': prop.get('eventname'),
            'country': country,
            'alert_level': (prop.get('alertlevel') or '').strip(),
            'severity_text': severity.get('severitytext') or severity.get('severity'),
            'from_date': prop.get('fromdate'),
            'to_date': prop.get('todate'),
            'is_current': prop.get('iscurrent'),
            'lat': lat,
            'lon': lon,
            'source': 'GDACS',
        })

    _cache['flood_events'] = {'data': floods, 'timestamp': utcnow()}
    return floods


def get_volcano_events():
    """Fetch open volcanic events near Calabarzon from NASA EONET (Earth
    Observatory Natural Event Tracker, https://eonet.gsfc.nasa.gov). No API
    key required.

    EONET is a global feed with no country filter, so events are matched
    against CALABARZON_BBOX using each event's most recent geometry point
    (e.g. Taal Volcano sits inside this box; volcanoes further from
    Calabarzon, such as Mayon or Kanlaon, will not match -- widen
    CALABARZON_BBOX if broader Philippine coverage is wanted later).
    Uses in-memory cache to reduce API calls.
    """
    cached = _cache.get('volcano_events')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    url = "https://eonet.gsfc.nasa.gov/api/v3/events?category=volcanoes&status=open"
    data = _fetch_json(url)
    if not data:
        return []

    volcanoes = []
    for event in data.get('events', []) or []:
        geometries = event.get('geometry') or []
        if not geometries:
            continue
        latest = geometries[-1] or {}
        coords = latest.get('coordinates') or []
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if lat is None or lon is None:
            continue
        if not (CALABARZON_BBOX['minlatitude'] <= lat <= CALABARZON_BBOX['maxlatitude']
                and CALABARZON_BBOX['minlongitude'] <= lon <= CALABARZON_BBOX['maxlongitude']):
            continue

        volcanoes.append({
            'event_id': event.get('id'),
            'title': event.get('title'),
            'date': latest.get('date'),
            'lat': lat,
            'lon': lon,
            'link': event.get('link'),
            'source': 'NASA EONET',
        })

    _cache['volcano_events'] = {'data': volcanoes, 'timestamp': utcnow()}
    return volcanoes
