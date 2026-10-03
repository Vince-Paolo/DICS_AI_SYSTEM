import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
    'thermal_hotspots': {'data': None, 'timestamp': None},
    'flood_footprints': {'data': None, 'timestamp': None},
    'typhoon_tracks_live': {'data': None, 'timestamp': None},
}
_cache_duration = 300  # 5 minutes
_CACHE_DB_PATH = Path(__file__).resolve().parents[1] / 'instance' / 'realtime_cache.sqlite3'


def _normalize_cache_timestamp(value):
    if isinstance(value, str):
        candidate = value.replace('Z', '+00:00')
        value = datetime.fromisoformat(candidate)
    if value is None:
        return None
    if getattr(value, 'tzinfo', None) is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _cache_row_key(name, subkey=None):
    if subkey is None:
        return name
    return f'{name}:{subkey}'


def _ensure_shared_cache_db():
    _CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_CACHE_DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS realtime_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()


def _read_shared_cache(key, default=None):
    _ensure_shared_cache_db()
    with sqlite3.connect(_CACHE_DB_PATH) as conn:
        row = conn.execute(
            'SELECT value FROM realtime_cache WHERE key = ?', (key,)
        ).fetchone()
    if row is None:
        return default
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):
        return default
    return payload if isinstance(payload, dict) else default


def _write_shared_cache(key, payload):
    _ensure_shared_cache_db()
    with sqlite3.connect(_CACHE_DB_PATH) as conn:
        conn.execute(
            'INSERT INTO realtime_cache (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, json.dumps(payload, default=str)),
        )
        conn.commit()


def _clear_shared_cache():
    _ensure_shared_cache_db()
    with sqlite3.connect(_CACHE_DB_PATH) as conn:
        conn.execute('DELETE FROM realtime_cache')
        conn.commit()


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

CALABARZON_CITY_COORDINATES = {
    'Lipa': (13.9411, 121.1631),
    'Batangas': (13.7565, 121.0583),
    'Tanauan': (14.0863, 121.1497),
    'Calamba': (14.2117, 121.1653),
    'San Pablo': (14.0683, 121.3256),
    'Lucena': (13.9373, 121.6172),
    'Tagaytay': (14.1153, 120.9621),
    'Imus': (14.4297, 120.9367),
    'Dasmariñas': (14.3294, 120.9367),
    'Cavite': (14.4791, 120.8970),
    'Taytay': (14.5588, 121.1329),
    'Antipolo': (14.5869, 121.1759),
    'Quezon': (14.1681, 121.6339),
    'Rizal': (14.6037, 121.3084),
    'Carmona': (14.3132, 121.0576),
    'Alaminos': (14.0639, 121.2465),
    'Nagcarlan': (14.1364, 121.4165),
    'San Fernando': (14.8127, 120.4642),
}

RAINFALL_WATCH_CITIES = ('lipa', 'batangas', 'calamba', 'lucena', 'tagaytay', 'san pablo')


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


def _get_firms_map_key():
    """NASA FIRMS MAP_KEY. Free registration at
    https://firms.modaps.eosdis.nasa.gov/api/map_key/ -- set FIRMS_MAP_KEY
    in the environment or .env. Thermal-hotspot fetching is skipped
    (returns []) when this isn't configured, same pattern as
    _get_openweather_api_key().
    """
    key = os.getenv("FIRMS_MAP_KEY")
    if key and key != "YOUR_FIRMS_MAP_KEY":
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


def _fetch_text(url):
    """Like _fetch_json, but for endpoints that return plain text/CSV
    (FIRMS's area API) rather than JSON.
    """
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.getcode() != 200:
                return None
            body = resp.read()
            if not body:
                return None
            return body.decode('utf-8')
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

    # Check cache first, preferring the shared cross-worker cache so all
    # Gunicorn workers see the same 5-minute TTL instead of doing duplicate
    # fetches against the upstream weather API.
    cached = _cache['weather'].get(canonical_city)
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared_weather = _read_shared_cache('weather', {})
    cached = shared_weather.get(canonical_city)
    if cached and cached.get('data') is not None and cached.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            _cache['weather'][canonical_city] = cached
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
        'lat': (data.get('coord') or {}).get('lat'),
        'lon': (data.get('coord') or {}).get('lon'),
        'temperature': data.get('main', {}).get('temp'),
        'humidity': data.get('main', {}).get('humidity'),
        'pressure': data.get('main', {}).get('pressure'),
        'wind_speed': data.get('wind', {}).get('speed'),
        'rainfall': rainfall,
        'weather': data.get('weather', [{}])[0].get('description'),
        'fetched_at': utcnow().isoformat() + 'Z'
    }
    # Cache the result by city in both the local process and the shared file
    # cache so other Gunicorn workers can reuse it within the TTL.
    now = utcnow()
    _cache['weather'][canonical_city] = {'data': result, 'timestamp': now}
    shared_weather = _read_shared_cache('weather', {})
    shared_weather[canonical_city] = {'data': result, 'timestamp': now.isoformat() + 'Z'}
    _write_shared_cache('weather', shared_weather)
    return result


def get_earthquake_data():
    """Fetch recent earthquake events from the Calabarzon region.
    Uses in-memory cache to reduce API calls.
    """
    # Check the local cache first, then the cross-worker shared cache so all
    # Gunicorn workers see the same quake feed for the 5-minute TTL.
    cached = _cache.get('earthquakes')
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared_quakes = _read_shared_cache('earthquakes', {'data': None, 'timestamp': None})
    cached = shared_quakes
    if cached and cached.get('data') is not None and cached.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            _cache['earthquakes'] = cached
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
        geometry = feat.get('geometry') or {}
        coordinates = geometry.get('coordinates') or []
        earthquakes.append({
            'event_id': feat.get('id'),
            'magnitude': prop.get('mag'),
            'place': prop.get('place'),
            'time': prop.get('time'),
            'lon': coordinates[0] if len(coordinates) >= 2 else None,
            'lat': coordinates[1] if len(coordinates) >= 2 else None,
        })
    # Cache the result in both the worker-local and shared cache stores.
    now = utcnow()
    _cache['earthquakes'] = {'data': earthquakes, 'timestamp': now}
    _write_shared_cache('earthquakes', {'data': earthquakes, 'timestamp': now.isoformat() + 'Z'})
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
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared_floods = _read_shared_cache('flood_events', {'data': None, 'timestamp': None})
    cached = shared_floods
    if cached and cached.get('data') is not None and cached.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            _cache['flood_events'] = cached
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
            'episode_id': prop.get('episodeid'),
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

    now = utcnow()
    _cache['flood_events'] = {'data': floods, 'timestamp': now}
    _write_shared_cache('flood_events', {'data': floods, 'timestamp': now.isoformat() + 'Z'})
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
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared_volcanoes = _read_shared_cache('volcano_events', {'data': None, 'timestamp': None})
    cached = shared_volcanoes
    if cached and cached.get('data') is not None and cached.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            _cache['volcano_events'] = cached
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

    now = utcnow()
    _cache['volcano_events'] = {'data': volcanoes, 'timestamp': now}
    _write_shared_cache('volcano_events', {'data': volcanoes, 'timestamp': now.isoformat() + 'Z'})
    return volcanoes


def get_thermal_hotspots():
    """Fetch near-real-time thermal-anomaly detections over Calabarzon from
    NASA FIRMS (Fire Information for Resource Management System,
    https://firms.modaps.eosdis.nasa.gov). Requires a free MAP_KEY --
    register at https://firms.modaps.eosdis.nasa.gov/api/map_key/ and set
    FIRMS_MAP_KEY in the environment or .env. Returns [] if no key is
    configured, same fallback pattern as get_weather_data() without an
    OpenWeather key.

    FIRMS detects VIIRS (NOAA-20/NOAA-21) thermal anomalies, which include
    both wildfires and volcanic hotspots -- the satellite instrument does
    not distinguish between the two. In DICS this is used as an
    independent satellite cross-check alongside NASA EONET's "open
    volcano event" status (see get_volcano_events()), not as a
    standalone hazard classifier: a hotspot near a monitored volcano is
    meaningful, one over farmland is very likely agricultural burning.
    The confidence field is passed through as-is so the UI/caller can
    filter or label accordingly. Uses the same in-memory + shared cache
    pattern as the other realtime fetchers.
    """
    map_key = _get_firms_map_key()
    if not map_key:
        return []

    cache_key = 'thermal_hotspots'
    cached = _cache.get(cache_key)
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared = _read_shared_cache(cache_key, {'data': None, 'timestamp': None})
    if shared and shared.get('data') is not None and shared.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(shared['timestamp']) < timedelta(seconds=_cache_duration):
            _cache[cache_key] = shared
            return shared['data']

    bbox = (
        f"{CALABARZON_BBOX['minlongitude']},{CALABARZON_BBOX['minlatitude']},"
        f"{CALABARZON_BBOX['maxlongitude']},{CALABARZON_BBOX['maxlatitude']}"
    )
    # VIIRS_NOAA20_NRT: ~375m resolution, near-real-time (within ~3 hours
    # of satellite pass). "/1" requests a 1-day window.
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/VIIRS_NOAA20_NRT/{bbox}/1"
    raw = _fetch_text(url)

    hotspots = []
    if raw:
        lines = raw.strip().splitlines()
        if len(lines) >= 2:
            header = [col.strip().lower() for col in lines[0].split(',')]
            idx = {name: header.index(name) for name in (
                'latitude', 'longitude', 'confidence', 'frp',
                'acq_date', 'acq_time', 'satellite',
            ) if name in header}
            for line in lines[1:]:
                cols = line.split(',')
                if len(cols) != len(header) or 'latitude' not in idx or 'longitude' not in idx:
                    continue
                try:
                    lat = float(cols[idx['latitude']])
                    lon = float(cols[idx['longitude']])
                except ValueError:
                    continue
                hotspots.append({
                    'lat': lat,
                    'lon': lon,
                    'confidence': cols[idx['confidence']] if 'confidence' in idx else None,
                    'frp': cols[idx['frp']] if 'frp' in idx else None,
                    'acq_date': cols[idx['acq_date']] if 'acq_date' in idx else None,
                    'acq_time': cols[idx['acq_time']] if 'acq_time' in idx else None,
                    'satellite': cols[idx['satellite']] if 'satellite' in idx else None,
                    'source': 'NASA FIRMS',
                })

    now = utcnow()
    _cache[cache_key] = {'data': hotspots, 'timestamp': now}
    _write_shared_cache(cache_key, {'data': hotspots, 'timestamp': now.isoformat() + 'Z'})
    return hotspots


def get_rainfall_watch():
    """Return current hourly rainfall observations for supported CALABARZON cities.

    ``get_weather_data`` provides the upstream weather cache and returns no
    reading when live weather is unavailable. Do not substitute sample values:
    an empty layer is more useful than presenting simulated rain as current.
    """
    rainfall_points = []
    for city_key in RAINFALL_WATCH_CITIES:
        city_name = CALABARZON_CITIES[city_key]
        weather = get_weather_data(city_key)
        if not weather:
            continue

        try:
            rainfall_mm = max(0.0, float(weather.get('rainfall') or 0))
        except (TypeError, ValueError):
            continue

        fallback_lat, fallback_lon = CALABARZON_CITY_COORDINATES[city_name]
        try:
            lat = float(weather.get('lat') if weather.get('lat') is not None else fallback_lat)
            lon = float(weather.get('lon') if weather.get('lon') is not None else fallback_lon)
        except (TypeError, ValueError):
            lat, lon = fallback_lat, fallback_lon

        if not (13.1 <= lat <= 14.8 and 120.4 <= lon <= 122.0):
            lat, lon = fallback_lat, fallback_lon

        if rainfall_mm == 0:
            status = 'No measurable rain'
        elif rainfall_mm < 2.5:
            status = 'Light rain'
        elif rainfall_mm < 7.5:
            status = 'Moderate rain'
        elif rainfall_mm < 30:
            status = 'Heavy rain'
        else:
            status = 'Intense rain'

        rainfall_points.append({
            'name': weather.get('city') or city_name,
            'lat': lat,
            'lon': lon,
            'rainfall_mm': rainfall_mm,
            'status': status,
            'source': 'OpenWeatherMap current weather',
            'fetched_at': weather.get('fetched_at'),
        })

    return rainfall_points


def get_typhoon_tracks():
    """Return current Philippines-relevant tropical cyclone tracks from GDACS."""
    cache_key = 'typhoon_tracks_live'
    cached = _cache.get(cache_key)
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared = _read_shared_cache(cache_key, {'data': None, 'timestamp': None})
    if shared and shared.get('data') is not None and shared.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(shared['timestamp']) < timedelta(seconds=_cache_duration):
            _cache[cache_key] = shared
            return shared['data']

    feed_url = 'https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP'
    feed = _fetch_json(feed_url) or {}
    typhoon_tracks = []
    philippines_bbox = {'min_lat': 0.0, 'max_lat': 30.0, 'min_lon': 112.0, 'max_lon': 140.0}

    for feature in feed.get('features', []):
        properties = feature.get('properties') or {}
        if str(properties.get('eventtype', '')).upper() != 'TC':
            continue
        if str(properties.get('iscurrent', '')).lower() != 'true':
            continue

        geometry = feature.get('geometry') or {}
        coordinates = geometry.get('coordinates') or []
        current_position = None
        if geometry.get('type') == 'Point' and len(coordinates) >= 2:
            try:
                current_lon, current_lat = float(coordinates[0]), float(coordinates[1])
                current_position = [current_lat, current_lon]
            except (TypeError, ValueError):
                pass

        affected_countries = properties.get('affectedcountries') or []
        affects_philippines = (
            'philippines' in str(properties.get('country') or '').lower()
            or any(str(country.get('iso3') or '').upper() == 'PHL' for country in affected_countries)
        )
        near_philippines = bool(current_position) and (
            philippines_bbox['min_lat'] <= current_position[0] <= philippines_bbox['max_lat']
            and philippines_bbox['min_lon'] <= current_position[1] <= philippines_bbox['max_lon']
        )
        if not affects_philippines and not near_philippines:
            continue

        event_id = properties.get('eventid')
        episode_id = properties.get('episodeid')
        if event_id is None or episode_id is None:
            continue

        geometry_url = 'https://www.gdacs.org/gdacsapi/api/polygons/getgeometry?' + urllib.parse.urlencode({
            'eventtype': 'TC',
            'eventid': event_id,
            'episodeid': episode_id,
        })
        track_geometry = _fetch_json(geometry_url) or {}
        segments = []
        for path_feature in track_geometry.get('features', []):
            path_properties = path_feature.get('properties') or {}
            path_geometry = path_feature.get('geometry') or {}
            if path_geometry.get('type') != 'LineString':
                continue
            path_coordinates = path_geometry.get('coordinates') or []
            points = []
            for point in path_coordinates:
                if len(point) < 2:
                    continue
                try:
                    lon, lat = float(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    points.append([lat, lon])
            if len(points) < 2:
                continue

            segments.append({
                'coordinates': points,
                'forecast': str(path_properties.get('forecast', '')).lower() == 'true',
                'source': path_properties.get('source') or properties.get('source'),
                'timestamp': path_properties.get('polygondate'),
            })

        severity = properties.get('severitydata') or {}
        try:
            wind_kph = round(float(severity.get('severity')))
        except (TypeError, ValueError):
            wind_kph = None

        typhoon_tracks.append({
            'event_id': event_id,
            'name': properties.get('eventname') or properties.get('name') or 'Tropical cyclone',
            'category': properties.get('alertlevel') or severity.get('severitytext') or 'Active',
            'pressure_hpa': None,
            'wind_kph': wind_kph,
            'center_lat': current_position[0] if current_position else None,
            'center_lon': current_position[1] if current_position else None,
            'track': current_position and [current_position] or [],
            'segments': segments,
            'source': properties.get('source') or 'GDACS',
            'updated_at': properties.get('datemodified') or properties.get('polygondate'),
        })

    now = utcnow()
    _cache[cache_key] = {'data': typhoon_tracks, 'timestamp': now}
    _write_shared_cache(cache_key, {'data': typhoon_tracks, 'timestamp': now.isoformat() + 'Z'})
    return typhoon_tracks


def get_flood_footprints():
    """Fetch flood-extent footprint polygons for current Philippine flood
    events from GDACS, sourced from EC Joint Research Centre satellite /
    hydrological flood modeling. No API key required.

    Builds on get_flood_events(): for each current event that has an
    episode_id, requests the polygon geometry documented at
    https://www.gdacs.org/gdacsapi/swagger/index.html
    (api/polygons/getgeometry?eventtype=FL&eventid=..&episodeid=..).
    Not every event exposes a footprint for every episode; those are
    skipped rather than raising, consistent with _fetch_json's
    fail-soft behaviour elsewhere in this module. Uses the same
    in-memory + shared cache pattern as get_flood_events().
    """
    cache_key = 'flood_footprints'
    cached = _cache.get(cache_key)
    if cached and cached['data'] is not None and cached['timestamp'] is not None:
        if utcnow() - _normalize_cache_timestamp(cached['timestamp']) < timedelta(seconds=_cache_duration):
            return cached['data']

    shared = _read_shared_cache(cache_key, {'data': None, 'timestamp': None})
    if shared and shared.get('data') is not None and shared.get('timestamp') is not None:
        if utcnow() - _normalize_cache_timestamp(shared['timestamp']) < timedelta(seconds=_cache_duration):
            _cache[cache_key] = shared
            return shared['data']

    footprints = []
    for flood in get_flood_events():
        event_id = flood.get('event_id')
        episode_id = flood.get('episode_id')
        if not event_id or not episode_id:
            continue
        url = (
            "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
            f"?eventtype=FL&eventid={event_id}&episodeid={episode_id}"
        )
        geo = _fetch_json(url)
        if not geo or not geo.get('features'):
            continue
        footprints.append({
            'event_id': event_id,
            'name': flood.get('name'),
            'alert_level': flood.get('alert_level'),
            'geojson': geo,
            'source': 'GDACS / EC-JRC',
        })

    now = utcnow()
    _cache[cache_key] = {'data': footprints, 'timestamp': now}
    _write_shared_cache(cache_key, {'data': footprints, 'timestamp': now.isoformat() + 'Z'})
    return footprints