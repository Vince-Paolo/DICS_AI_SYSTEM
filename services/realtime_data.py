import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# OpenWeatherMap API key must be provided via environment variable.
# Example: set OPENWEATHER_API_KEY=your_real_key before running the app.
# This module also supports a local .env file in the project root.

# Simple in-memory cache for API responses (reduces duplicate calls)
_cache = {
    'weather': {'data': None, 'timestamp': None},
    'earthquakes': {'data': None, 'timestamp': None}
}
_cache_duration = 300  # 5 minutes
CALABARZON_CITIES = {
    'lipa', 'batangas', 'tanauan', 'calamba', 'san pablo', 'lucena',
    'tagaytay', 'imus', 'dasmariñas', 'cavite', 'taytay', 'antipolo',
    'quezon', 'rizal', 'carmona', 'alaminos', 'nagcarlan', 'san fernando'
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
    if city.lower() not in CALABARZON_CITIES:
        return None

    # Check cache first
    cached = _cache.get('weather')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if datetime.utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    api_key = _get_openweather_api_key()
    if not api_key:
        return None

    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city},PH&appid={api_key}&units=metric"
    )
    data = _fetch_json(url)
    if not data:
        return None

    rainfall = 0
    if 'rain' in data:
        rainfall = data['rain'].get('1h', 0) or 0

    result = {
        'city': city,
        'temperature': data.get('main', {}).get('temp'),
        'humidity': data.get('main', {}).get('humidity'),
        'pressure': data.get('main', {}).get('pressure'),
        'wind_speed': data.get('wind', {}).get('speed'),
        'rainfall': rainfall,
        'weather': data.get('weather', [{}])[0].get('description'),
        'fetched_at': datetime.utcnow().isoformat() + 'Z'
    }
    # Cache the result
    _cache['weather'] = {'data': result, 'timestamp': datetime.utcnow()}
    return result


def get_earthquake_data():
    """Fetch recent earthquake events from the Calabarzon region.
    Uses in-memory cache to reduce API calls.
    """
    # Check cache first
    cached = _cache.get('earthquakes')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if datetime.utcnow() - cached['timestamp'] < timedelta(seconds=_cache_duration):
            return cached['data']

    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=geojson&minlatitude={CALABARZON_BBOX['minlatitude']}"
        f"&maxlatitude={CALABARZON_BBOX['maxlatitude']}"
        f"&minlongitude={CALABARZON_BBOX['minlongitude']}"
        f"&maxlongitude={CALABARZON_BBOX['maxlongitude']}"
        "&orderby=time&limit=10"
    )
    data = _fetch_json(url)
    if not data:
        return []

    earthquakes = []
    for feat in data.get('features', []):
        prop = feat.get('properties', {})
        geom = feat.get('geometry', {}) or {}
        coords = geom.get('coordinates') or [None, None, None]
        lon, lat, depth = (coords + [None, None, None])[:3]
        earthquakes.append({
            'magnitude': prop.get('mag'),
            'place': prop.get('place'),
            'time': prop.get('time'),
            'latitude': lat,
            'longitude': lon,
            'depth_km': depth,
        })
    # Cache the result
    _cache['earthquakes'] = {'data': earthquakes, 'timestamp': datetime.utcnow()}
    return earthquakes
