import os
from datetime import datetime
from pathlib import Path

# OpenWeatherMap API key must be provided via environment variable.
# Example: set OPENWEATHER_API_KEY=your_real_key before running the app.
# This module also supports a local .env file in the project root.
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


def get_weather_data(city="Lipa"):
    """Fetch current weather for a Calabarzon city.
    Only returns data for Calabarzon locations.
    """
    if city.lower() not in CALABARZON_CITIES:
        return None

    api_key = _get_openweather_api_key()
    if not api_key:
        return None

    try:
        import requests
    except ImportError:
        return None

    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={city},PH&appid={api_key}&units=metric"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        rainfall = 0
        if 'rain' in data:
            rainfall = data['rain'].get('1h', 0) or 0

        return {
            'city': city,
            'temperature': data.get('main', {}).get('temp'),
            'humidity': data.get('main', {}).get('humidity'),
            'pressure': data.get('main', {}).get('pressure'),
            'wind_speed': data.get('wind', {}).get('speed'),
            'rainfall': rainfall,
            'weather': data.get('weather', [{}])[0].get('description'),
            'fetched_at': datetime.utcnow().isoformat() + 'Z'
        }
    except Exception:
        return None


def get_earthquake_data():
    """Fetch recent earthquake events from the Calabarzon region."""
    try:
        import requests
    except ImportError:
        return []

    try:
        url = (
            "https://earthquake.usgs.gov/fdsnws/event/1/query"
            f"?format=geojson&minlatitude={CALABARZON_BBOX['minlatitude']}"
            f"&maxlatitude={CALABARZON_BBOX['maxlatitude']}"
            f"&minlongitude={CALABARZON_BBOX['minlongitude']}"
            f"&maxlongitude={CALABARZON_BBOX['maxlongitude']}"
            "&orderby=time&limit=10"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        earthquakes = []
        for feat in data.get('features', []):
            prop = feat.get('properties', {})
            earthquakes.append({
                'magnitude': prop.get('mag'),
                'place': prop.get('place'),
                'time': prop.get('time')
            })
        return earthquakes
    except Exception:
        return []
