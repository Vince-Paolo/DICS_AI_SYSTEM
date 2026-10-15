import os
import json
import unittest
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
# See tests/test_responder_routes.py for why this must be set before `app` is imported.
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_external_hazard_feeds.db'))
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

from app import app, db
from models import Alert, AuditEvent, Incident, User
from seed.demo_data import GEOGRAPHY_DATA_PATH, seed_geography_data
import services.realtime_data as realtime_data
import scheduler

# Sample payloads shaped exactly per each provider's documented schema --
# GDACS: https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf
# (eventtype/alertlevel/severitydata/country field names) and the
# python-aio-georss-gdacs field mapping.
# EONET v3: https://eonet.gsfc.nasa.gov/api/v3/events?category=volcanoes
GDACS_SAMPLE_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [121.0, 14.1]},
            "properties": {
                "eventid": 1234567,
                "episodeid": 7654321,
                "eventtype": "FL",
                "eventname": "Flood in Batangas",
                "country": "Philippines",
                "alertlevel": "Orange",
                "severitydata": {"severity": 3, "severitytext": "3m water height"},
                "fromdate": "2026-08-10T00:00:00",
                "todate": "2026-08-13T00:00:00",
                "iscurrent": True,
            },
        },
        {
            # Different country -- must be filtered out.
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [100.0, 10.0]},
            "properties": {
                "eventid": 999,
                "eventtype": "FL",
                "eventname": "Flood in Thailand",
                "country": "Thailand",
                "alertlevel": "Red",
                "severitydata": {},
                "fromdate": "2026-08-10T00:00:00",
                "todate": "2026-08-13T00:00:00",
                "iscurrent": True,
            },
        },
        {
            # Right country, wrong event type -- must be filtered out.
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [121.0, 14.1]},
            "properties": {
                "eventid": 555,
                "eventtype": "EQ",
                "eventname": "Earthquake",
                "country": "Philippines",
                "alertlevel": "Green",
                "severitydata": {},
                "fromdate": "2026-08-10T00:00:00",
                "todate": "2026-08-13T00:00:00",
                "iscurrent": True,
            },
        },
    ],
}

EONET_SAMPLE_RESPONSE = {
    "title": "EONET Volcanoes",
    "events": [
        {
            "id": "EONET_1111",
            "title": "Taal Volcano",
            "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1111",
            "categories": [{"id": "volcanoes", "title": "Volcanoes"}],
            "geometry": [
                {"date": "2026-08-11T00:00:00Z", "type": "Point", "coordinates": [120.99, 14.00]},
            ],
        },
        {
            # Outside CALABARZON_BBOX (e.g. Mayon, Bicol) -- must be filtered out.
            "id": "EONET_2222",
            "title": "Mayon Volcano",
            "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_2222",
            "categories": [{"id": "volcanoes", "title": "Volcanoes"}],
            "geometry": [
                {"date": "2026-08-11T00:00:00Z", "type": "Point", "coordinates": [123.685, 13.257]},
            ],
        },
    ],
}


# NASA FIRMS Area API sample -- VIIRS_NOAA20_NRT CSV response shape per
# https://firms.modaps.eosdis.nasa.gov/api/area/
FIRMS_CSV_SAMPLE = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "confidence,version,bright_ti5,frp,daynight\n"
    "14.00,120.99,330.5,0.4,0.4,2026-08-12,0512,N20,n,2.0NRT,290.1,12.3,N\n"
    "14.60,121.10,301.2,0.4,0.4,2026-08-12,0512,N20,l,2.0NRT,280.0,3.1,N\n"
)

GDACS_FOOTPRINT_SAMPLE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[121.0, 14.0], [121.2, 14.0], [121.2, 14.2], [121.0, 14.2], [121.0, 14.0]]]},
            "properties": {},
        },
    ],
}


class ExternalHazardFeedParsingTestCase(unittest.TestCase):
    """Unit tests for the fetch/filter/parse logic, independent of network access."""

    def setUp(self):
        realtime_data._clear_shared_cache()
        realtime_data._cache['earthquakes'] = {'data': None, 'timestamp': None}
        realtime_data._cache['flood_events'] = {'data': None, 'timestamp': None}
        realtime_data._cache['volcano_events'] = {'data': None, 'timestamp': None}
        realtime_data._cache['thermal_hotspots'] = {'data': None, 'timestamp': None}
        realtime_data._cache['flood_footprints'] = {'data': None, 'timestamp': None}
        realtime_data._cache['rainfall_watch_san_pablo'] = {'data': None, 'timestamp': None}
        realtime_data._cache['typhoon_tracks'] = {'data': None, 'timestamp': None}

    def test_get_rainfall_watch_parses_live_open_meteo_data(self):
        open_meteo_data = [
            {
                'current': {'precipitation': 8.25},
                'hourly': {'precipitation': [None, 1.2, 2.3]},
            }
            for _ in realtime_data.SAN_PABLO_BARANGAYS
        ]
        with patch.object(realtime_data, '_fetch_json', return_value=open_meteo_data) as fetch:
            rainfall = realtime_data.get_rainfall_watch()

        self.assertEqual(len(rainfall), 80)
        self.assertEqual(rainfall[0]['name'], realtime_data.SAN_PABLO_BARANGAYS[0]['name'])
        self.assertEqual(rainfall[0]['barangay_code'], realtime_data.SAN_PABLO_BARANGAYS[0]['code'])
        self.assertEqual(rainfall[0]['lat'], realtime_data.SAN_PABLO_BARANGAYS[0]['lat'])
        self.assertEqual(rainfall[0]['lon'], realtime_data.SAN_PABLO_BARANGAYS[0]['lon'])
        self.assertEqual(rainfall[0]['rainfall_mm'], 8.2)
        self.assertEqual(rainfall[0]['rainfall_24h_mm'], 3.5)
        self.assertEqual(rainfall[0]['status'], 'Heavy rain (Yellow)')
        self.assertEqual(rainfall[0]['source'], 'Open-Meteo')
        params = parse_qs(urlparse(fetch.call_args.args[0]).query)
        self.assertEqual(len(params['latitude'][0].split(',')), 80)
        self.assertEqual(len(params['longitude'][0].split(',')), 80)
        self.assertEqual(params['past_hours'], ['24'])

    def test_rainfall_cache_uses_15_minute_interval(self):
        now = datetime(2026, 10, 12, 12, 0)
        cached_data = [{'name': 'Cached rainfall'}]
        realtime_data._write_shared_cache(
            'rainfall_watch_san_pablo',
            {
                'data': cached_data,
                'timestamp': (now - timedelta(minutes=10)).isoformat() + 'Z',
            },
        )

        with patch.object(realtime_data, 'utcnow', return_value=now), \
                patch.object(realtime_data, '_fetch_json') as fetch:
            rainfall = realtime_data.get_rainfall_watch()

        self.assertEqual(rainfall, cached_data)
        fetch.assert_not_called()

    def test_rainfall_cache_refreshes_after_15_minutes(self):
        now = datetime(2026, 10, 12, 12, 0)
        realtime_data._cache['rainfall_watch_san_pablo'] = {
            'data': [{'name': 'Stale rainfall'}],
            'timestamp': now - timedelta(minutes=16),
        }
        fresh_data = [
            {'current': {'precipitation': 1.0}, 'hourly': {'precipitation': [1.0]}}
            for _ in realtime_data.SAN_PABLO_BARANGAYS
        ]

        with patch.object(realtime_data, 'utcnow', return_value=now), \
                patch.object(realtime_data, '_fetch_json', return_value=fresh_data) as fetch:
            rainfall = realtime_data.get_rainfall_watch()

        self.assertEqual(len(rainfall), 80)
        self.assertEqual(rainfall[0]['rainfall_mm'], 1.0)
        fetch.assert_called_once()

    def test_rainfall_centroids_cover_all_san_pablo_barangays(self):
        barangays = realtime_data.SAN_PABLO_BARANGAYS
        self.assertEqual(len(barangays), 80)
        self.assertEqual(len({barangay['code'] for barangay in barangays}), 80)
        self.assertTrue(all(13.9 <= item['lat'] <= 14.2 for item in barangays))
        self.assertTrue(all(121.2 <= item['lon'] <= 121.5 for item in barangays))
        with GEOGRAPHY_DATA_PATH.open(encoding='utf-8') as geography_file:
            geography = json.load(geography_file)
        expected_codes = {
            barangay['code']
            for province in geography if province['name'].casefold() == 'laguna'
            for municipality in province['municipalities'] if municipality['code'] == '043424'
            for barangay in municipality['barangays']
        }
        self.assertSetEqual({barangay['code'] for barangay in barangays}, expected_codes)

    def test_get_rainfall_watch_returns_empty_list_on_fetch_failure(self):
        with patch.object(realtime_data, '_fetch_json', return_value=None):
            rainfall = realtime_data.get_rainfall_watch()
        self.assertEqual(rainfall, [])

    def test_rainfall_fetch_failure_is_throttled_for_2_minutes(self):
        now = datetime(2026, 10, 12, 12, 0)

        with patch.object(realtime_data, 'utcnow', return_value=now), \
                patch.object(realtime_data, '_fetch_json', return_value=None) as fetch:
            self.assertEqual(realtime_data.get_rainfall_watch(), [])
            self.assertEqual(realtime_data.get_rainfall_watch(), [])

        fetch.assert_called_once()

    def test_rainfall_failure_cache_retries_after_2_minutes(self):
        now = [datetime(2026, 10, 12, 12, 0)]
        fresh_data = [
            {'current': {'precipitation': 1.0}, 'hourly': {'precipitation': [1.0]}}
            for _ in realtime_data.SAN_PABLO_BARANGAYS
        ]

        with patch.object(realtime_data, 'utcnow', side_effect=lambda: now[0]), \
                patch.object(realtime_data, '_fetch_json', side_effect=[None, fresh_data]) as fetch:
            self.assertEqual(realtime_data.get_rainfall_watch(), [])
            now[0] += timedelta(minutes=3)
            rainfall = realtime_data.get_rainfall_watch()

        self.assertEqual(len(rainfall), 80)
        self.assertEqual(rainfall[0]['rainfall_mm'], 1.0)
        self.assertEqual(fetch.call_count, 2)

    def test_get_rainfall_watch_does_not_return_partial_feed_results(self):
        with patch.object(realtime_data, '_fetch_json', return_value=[{'current': {'precipitation': 1}}]):
            rainfall = realtime_data.get_rainfall_watch()
        self.assertEqual(rainfall, [])

    def test_get_typhoon_tracks_filters_active_par_events_and_parses_tracks(self):
        gdacs_events = {
            'features': [
                {
                    'geometry': {'type': 'Point', 'coordinates': [125.0, 15.0]},
                    'properties': {
                        'eventid': 123, 'episodeid': 456, 'eventtype': 'TC',
                        'iscurrent': True, 'eventname': 'Tropical Storm Test',
                        'alertlevel': 'Orange',
                        'severitydata': {'severity': 85, 'severitytext': 'Tropical storm'},
                    },
                },
                {
                    'geometry': {'type': 'Point', 'coordinates': [125.0, 15.0]},
                    'properties': {'eventtype': 'TC', 'iscurrent': False},
                },
                {
                    'geometry': {'type': 'Point', 'coordinates': [140.0, 15.0]},
                    'properties': {'eventtype': 'TC', 'iscurrent': True},
                },
            ],
        }
        track_geojson = {
            'features': [{
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[124.0, 14.0], [125.0, 15.0]],
                },
            }],
        }

        def fetch_json(url):
            return track_geojson if 'polygons/getgeometry' in url else gdacs_events

        with patch.object(realtime_data, '_fetch_json', side_effect=fetch_json):
            storms = realtime_data.get_typhoon_tracks()

        self.assertEqual(len(storms), 1)
        self.assertEqual(storms[0]['name'], 'Tropical Storm Test')
        self.assertEqual(storms[0]['wind_kph'], 85)
        self.assertIsNone(storms[0]['pressure_hpa'])
        self.assertEqual(storms[0]['track'], [[14.0, 124.0], [15.0, 125.0]])
        self.assertEqual(storms[0]['source'], 'GDACS (JTWC/NOAA-based)')

    def test_get_typhoon_tracks_returns_empty_list_on_fetch_failure(self):
        with patch.object(realtime_data, '_fetch_json', return_value=None):
            storms = realtime_data.get_typhoon_tracks()
        self.assertEqual(storms, [])

    def test_get_flood_events_filters_to_philippine_floods_only(self):
        with patch.object(realtime_data, '_fetch_json', return_value=GDACS_SAMPLE_RESPONSE):
            floods = realtime_data.get_flood_events()

        self.assertEqual(len(floods), 1)
        flood = floods[0]
        self.assertEqual(flood['event_id'], 1234567)
        self.assertEqual(flood['country'], 'Philippines')
        self.assertEqual(flood['alert_level'], 'Orange')
        self.assertEqual(flood['lat'], 14.1)
        self.assertEqual(flood['lon'], 121.0)
        self.assertEqual(flood['source'], 'GDACS')

    def test_get_flood_events_returns_empty_list_on_fetch_failure(self):
        with patch.object(realtime_data, '_fetch_json', return_value=None):
            floods = realtime_data.get_flood_events()
        self.assertEqual(floods, [])

    def test_get_volcano_events_filters_to_calabarzon_bbox(self):
        with patch.object(realtime_data, '_fetch_json', return_value=EONET_SAMPLE_RESPONSE):
            volcanoes = realtime_data.get_volcano_events()

        self.assertEqual(len(volcanoes), 1)
        self.assertEqual(volcanoes[0]['title'], 'Taal Volcano')
        self.assertEqual(volcanoes[0]['event_id'], 'EONET_1111')
        self.assertEqual(volcanoes[0]['source'], 'NASA EONET')

    def test_get_volcano_events_returns_empty_list_on_fetch_failure(self):
        with patch.object(realtime_data, '_fetch_json', return_value=None):
            volcanoes = realtime_data.get_volcano_events()
        self.assertEqual(volcanoes, [])

    def test_get_thermal_hotspots_returns_empty_list_without_map_key(self):
        with patch.object(realtime_data, '_get_firms_map_key', return_value=None), \
             patch.object(realtime_data, '_fetch_text') as mock_fetch:
            hotspots = realtime_data.get_thermal_hotspots()
        self.assertEqual(hotspots, [])
        mock_fetch.assert_not_called()

    def test_get_thermal_hotspots_parses_firms_csv(self):
        with patch.object(realtime_data, '_get_firms_map_key', return_value='fake-key'), \
             patch.object(realtime_data, '_fetch_text', return_value=FIRMS_CSV_SAMPLE):
            hotspots = realtime_data.get_thermal_hotspots()

        self.assertEqual(len(hotspots), 2)
        self.assertEqual(hotspots[0]['lat'], 14.00)
        self.assertEqual(hotspots[0]['lon'], 120.99)
        self.assertEqual(hotspots[0]['confidence'], 'n')
        self.assertEqual(hotspots[0]['source'], 'NASA FIRMS')

    def test_get_thermal_hotspots_returns_empty_list_on_fetch_failure(self):
        with patch.object(realtime_data, '_get_firms_map_key', return_value='fake-key'), \
             patch.object(realtime_data, '_fetch_text', return_value=None):
            hotspots = realtime_data.get_thermal_hotspots()
        self.assertEqual(hotspots, [])

    def test_get_flood_footprints_fetches_polygon_for_events_with_episode_id(self):
        def fake_fetch_json(url):
            if 'geteventlist' in url:
                return GDACS_SAMPLE_RESPONSE
            if 'polygons/getgeometry' in url:
                self.assertIn('eventid=1234567', url)
                self.assertIn('episodeid=7654321', url)
                return GDACS_FOOTPRINT_SAMPLE
            return None

        with patch.object(realtime_data, '_fetch_json', side_effect=fake_fetch_json):
            footprints = realtime_data.get_flood_footprints()

        self.assertEqual(len(footprints), 1)
        self.assertEqual(footprints[0]['event_id'], 1234567)
        self.assertEqual(footprints[0]['source'], 'GDACS / EC-JRC')
        self.assertEqual(footprints[0]['geojson'], GDACS_FOOTPRINT_SAMPLE)

    def test_get_flood_footprints_skips_events_without_episode_id(self):
        no_episode_response = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [121.0, 14.1]},
                "properties": {
                    "eventid": 42, "eventtype": "FL", "eventname": "No episode",
                    "country": "Philippines", "alertlevel": "Green",
                    "severitydata": {}, "fromdate": "", "todate": "", "iscurrent": True,
                },
            }],
        }
        with patch.object(realtime_data, '_fetch_json', return_value=no_episode_response):
            footprints = realtime_data.get_flood_footprints()
        self.assertEqual(footprints, [])

    def test_earthquake_cache_is_persisted_for_other_workers(self):
        feature = {
            'id': 'us7000abcd',
            'geometry': {'type': 'Point', 'coordinates': [121.1, 14.2]},
            'properties': {
                'mag': 5.2,
                'place': 'Lipa, Batangas',
                'time': 1720000000000,
            },
        }
        with patch.object(realtime_data, '_fetch_json', return_value={'features': [feature]}):
            data = realtime_data.get_earthquake_data()

        self.assertEqual(len(data), 1)
        persisted = realtime_data._read_shared_cache('earthquakes')
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted['data'][0]['event_id'], 'us7000abcd')
        self.assertEqual(persisted['data'][0]['place'], 'Lipa, Batangas')
        self.assertEqual(persisted['data'][0]['lat'], 14.2)
        self.assertEqual(persisted['data'][0]['lon'], 121.1)


class ExternalHazardMonitorTestCase(unittest.TestCase):
    """Tests for dispatcher-only alerts generated from regional event feeds."""

    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            seed_geography_data()
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def test_monitor_floods_gdacs_creates_dispatcher_alert_not_incident(self):
        parsed_flood = {
            'event_id': 1234567, 'name': 'Flood in Batangas', 'country': 'Philippines',
            'alert_level': 'Orange', 'severity_text': '3m water height',
            'from_date': '2026-08-10', 'to_date': '2026-08-13',
            'is_current': True, 'lat': 14.05, 'lon': 121.15, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                created = scheduler.monitor_floods_gdacs(app)
            self.assertTrue(created)
            alert = Alert.query.filter_by(external_event_id='gdacs:1234567').one()
            self.assertEqual(alert.audience, 'DISPATCHER')
            self.assertEqual(alert.severity, 'HIGH')
            self.assertIn('GDACS', alert.message)
            self.assertIn('km from San Pablo City', alert.message)
            self.assertIsNone(alert.incident_id)
            self.assertEqual(Incident.query.count(), 0)
            self.assertEqual(AuditEvent.query.filter_by(entity_id=alert.id, action='EXTERNAL_EVENT_LOGGED').count(), 1)

    def test_monitor_floods_gdacs_logs_nearby_green_as_low_priority_alert(self):
        parsed_flood = {
            'event_id': 2, 'name': 'Minor Flood', 'country': 'Philippines',
            'alert_level': 'Green', 'severity_text': '', 'from_date': '', 'to_date': '',
            'is_current': True, 'lat': 14.05, 'lon': 121.15, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                created = scheduler.monitor_floods_gdacs(app)
            self.assertTrue(created)
            alert = Alert.query.filter_by(external_event_id='gdacs:2').one()
            self.assertEqual(alert.severity, 'LOW')
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_floods_gdacs_deduplicates_by_external_event_id(self):
        parsed_flood = {
            'event_id': 3, 'name': 'Repeat Flood', 'country': 'Philippines',
            'alert_level': 'Red', 'severity_text': '', 'from_date': '', 'to_date': '',
            'is_current': True, 'lat': 14.05, 'lon': 121.15, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                first = scheduler.monitor_floods_gdacs(app)
                second = scheduler.monitor_floods_gdacs(app)
            self.assertTrue(first)
            self.assertFalse(second)
            count = Alert.query.filter_by(external_event_id='gdacs:3').count()
            self.assertEqual(count, 1)
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_earthquakes_logs_nearby_event_once_without_incident(self):
        """Regression test for the exact bug reported against production: the
        same real earthquake (a single USGS event) kept appearing in the
        "10 most recent" feed for CALABARZON days apart, and each time the
        prior incident's created_at aged past the old 6-hour window, a brand
        new duplicate incident got created for the same physical event.
        De-duping must be keyed on the quake's own USGS event id, not a
        rolling wall-clock window from our own row's created_at."""
        quake = {
            'event_id': 'us7000example', 'magnitude': 5.2,
            'place': 'Batangas, Philippines', 'time': 1723000000000,
            'lat': 13.75, 'lon': 121.05,
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[quake]):
                first = scheduler.monitor_earthquakes(app)
                self.assertTrue(first)

                second = scheduler.monitor_earthquakes(app)
            self.assertFalse(second)
            alert = Alert.query.filter_by(external_event_id='usgs:us7000example').one()
            self.assertEqual(alert.audience, 'DISPATCHER')
            self.assertIn('Batangas', alert.title)
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_earthquakes_uses_magnitude_for_dispatcher_alert_severity(self):
        quake = {
            'event_id': 'us7000forecast', 'magnitude': 5.8,
            'place': 'Batangas, Philippines', 'time': 1723000000000,
            'lat': 13.75, 'lon': 121.05,
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[quake]):
                created = scheduler.monitor_earthquakes(app)

            self.assertTrue(created)
            alert = Alert.query.filter_by(external_event_id='usgs:us7000forecast').one()
            self.assertEqual(alert.severity, 'HIGH')
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_earthquakes_logs_distinct_ids_as_distinct_alerts(self):
        """A different USGS event id at a similar location (e.g. an
        aftershock) must still create its own incident -- the fix must not
        become a blanket "one incident per location, ever" suppression."""
        first_quake = {
            'event_id': 'us7000first', 'magnitude': 5.2,
            'place': 'Batangas, Philippines', 'time': 1723000000000,
            'lat': 13.75, 'lon': 121.05,
        }
        second_quake = {
            'event_id': 'us7000second', 'magnitude': 4.8,
            'place': 'Batangas, Philippines', 'time': 1723003600000,
            'lat': 13.75, 'lon': 121.05,
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[first_quake]):
                scheduler.monitor_earthquakes(app)
            with patch.object(scheduler, 'get_earthquake_data', return_value=[second_quake]):
                created = scheduler.monitor_earthquakes(app)
            self.assertTrue(created)
            count = Alert.query.filter(Alert.external_event_id.in_(['usgs:us7000first', 'usgs:us7000second'])).count()
            self.assertEqual(count, 2)
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_volcanoes_eonet_deduplicates_dispatcher_alert(self):
        parsed_volcano = {
            'event_id': 'EONET_9999', 'title': 'Taal Volcano', 'date': '2026-08-07T00:00:00Z',
            'lat': 14.00, 'lon': 120.99, 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/EONET_9999',
            'source': 'NASA EONET',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[parsed_volcano]):
                first = scheduler.monitor_volcanoes_eonet(app)
                second = scheduler.monitor_volcanoes_eonet(app)
            self.assertFalse(second)
            self.assertTrue(first)
            count = Alert.query.filter_by(external_event_id='eonet:EONET_9999').count()
            self.assertEqual(count, 1)
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_volcanoes_eonet_creates_high_dispatcher_alert(self):
        parsed_volcano = {
            'event_id': 'EONET_1111', 'title': 'Taal Volcano', 'date': '2026-08-11T00:00:00Z',
            'lat': 14.00, 'lon': 120.99, 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1111',
            'source': 'NASA EONET',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[parsed_volcano]):
                created = scheduler.monitor_volcanoes_eonet(app)
            self.assertTrue(created)
            alert = Alert.query.filter_by(external_event_id='eonet:EONET_1111').one()
            self.assertEqual(alert.audience, 'DISPATCHER')
            self.assertEqual(alert.severity, 'HIGH')
            self.assertIn('EONET', alert.message)
            self.assertIsNone(alert.incident_id)
            self.assertEqual(Incident.query.count(), 0)

    def test_monitor_volcanoes_eonet_no_events_returns_false(self):
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[]):
                created = scheduler.monitor_volcanoes_eonet(app)
            self.assertFalse(created)

    def test_regional_event_handlers_ignore_events_outside_alert_radius(self):
        far_flood = {
            'event_id': 42, 'name': 'Flood outside radius', 'country': 'Philippines',
            'alert_level': 'Red', 'is_current': True, 'lat': 15.5, 'lon': 121.0,
        }
        distant_volcano = {
            'event_id': 'EONET_FAR', 'title': 'Distant Volcano',
            'lat': 16.0, 'lon': 122.0,
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[far_flood]):
                self.assertFalse(scheduler.monitor_floods_gdacs(app))
            with patch.object(scheduler, 'get_volcano_events', return_value=[distant_volcano]):
                self.assertFalse(scheduler.monitor_volcanoes_eonet(app))
            self.assertEqual(Alert.query.filter_by(audience='DISPATCHER').count(), 0)

    def test_monitor_hazards_checks_regional_feeds_without_local_weather(self):
        with patch.object(scheduler, 'monitor_earthquakes') as earthquakes, \
             patch.object(scheduler, 'monitor_floods_gdacs') as floods, \
             patch.object(scheduler, 'monitor_volcanoes_eonet') as volcanoes, \
             patch.object(scheduler, 'get_all_weather_data', return_value={}):
            scheduler._monitor_hazards_once(app)
        earthquakes.assert_called_once_with(app)
        floods.assert_called_once_with(app)
        volcanoes.assert_called_once_with(app)

    def test_regional_alerts_are_visible_to_dispatchers_but_not_citizens(self):
        with self.app.app_context():
            dispatcher = User(
                username='regional-alert-dispatcher',
                email='regional-alert-dispatcher@example.test',
                password='test-password',
                role='dispatcher',
            )
            citizen = User(
                username='regional-alert-citizen',
                email='regional-alert-citizen@example.test',
                password='test-password',
                role='citizen',
            )
            db.session.add_all([dispatcher, citizen])
            dispatcher_alert = Alert(
                audience='DISPATCHER',
                external_event_id='usgs:visibility-test',
                title='Dispatcher-only regional earthquake',
                message='Nearby earthquake for dispatcher review.',
                severity='HIGH',
                status='ACTIVE',
            )
            db.session.add_all([
                dispatcher_alert,
                Alert(
                    audience='PUBLIC',
                    title='Public safety advisory',
                    message='Official citizen-facing advisory.',
                    severity='MEDIUM',
                    status='ACTIVE',
                ),
            ])
            db.session.commit()
            dispatcher_alert_id = dispatcher_alert.id

        client = self.app.test_client()
        with client.session_transaction() as session:
            session['username'] = 'regional-alert-dispatcher'
            session['role'] = 'dispatcher'
        dispatcher_response = client.get('/dispatcher/alerts')
        self.assertEqual(dispatcher_response.status_code, 200)
        self.assertIn(b'Dispatcher-only regional earthquake', dispatcher_response.data)
        self.assertNotIn(b'Public safety advisory', dispatcher_response.data)
        resolved = client.post(f'/dispatcher/alerts/{dispatcher_alert_id}/resolve')
        self.assertEqual(resolved.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Alert, dispatcher_alert_id).status, 'RESOLVED')

        with client.session_transaction() as session:
            session.clear()
            session['username'] = 'regional-alert-citizen'
            session['role'] = 'citizen'
        citizen_response = client.get('/citizen-alerts')
        self.assertEqual(citizen_response.status_code, 200)
        self.assertIn(b'Public safety advisory', citizen_response.data)
        self.assertNotIn(b'Dispatcher-only regional earthquake', citizen_response.data)

        with client.session_transaction() as session:
            session.clear()
            session['username'] = 'regional-alert-citizen'
            session['role'] = 'citizen'
        forbidden_response = client.get('/dispatcher/alerts')
        self.assertEqual(forbidden_response.status_code, 403)

    def test_incident_display_time_prefers_event_time_over_created_at(self):
        with self.app.app_context():
            with_event_time = Incident(
                hazard_type='earthquake', location='X', message='m', status='ACTIVE',
                created_at=datetime(2026, 8, 13, 6, 41), event_time=datetime(2026, 8, 7, 14, 22),
            )
            without_event_time = Incident(
                hazard_type='flood', location='Y', message='m', status='NEW',
                created_at=datetime(2026, 8, 15, 9, 0),
            )
            self.assertEqual(with_event_time.display_time, datetime(2026, 8, 7, 14, 22))
            self.assertEqual(without_event_time.display_time, datetime(2026, 8, 15, 9, 0))


if __name__ == '__main__':
    unittest.main()