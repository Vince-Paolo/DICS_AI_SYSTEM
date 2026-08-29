import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
# See tests/test_responder_routes.py for why this must be set before `app` is imported.
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_external_hazard_feeds.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from app import app, db
from models import Incident
from seed.demo_data import seed_geography_data
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


class ExternalHazardFeedParsingTestCase(unittest.TestCase):
    """Unit tests for the fetch/filter/parse logic, independent of network access."""

    def setUp(self):
        realtime_data._cache['flood_events'] = {'data': None, 'timestamp': None}
        realtime_data._cache['volcano_events'] = {'data': None, 'timestamp': None}

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


class ExternalHazardMonitorTestCase(unittest.TestCase):
    """Tests for the scheduler-side deterministic incident creation."""

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

    def test_monitor_floods_gdacs_creates_incident_for_orange_alert(self):
        parsed_flood = {
            'event_id': 1234567, 'name': 'Flood in Batangas', 'country': 'Philippines',
            'alert_level': 'Orange', 'severity_text': '3m water height',
            'from_date': '2026-08-10', 'to_date': '2026-08-13',
            'is_current': True, 'lat': 14.1, 'lon': 121.0, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                created = scheduler.monitor_floods_gdacs(app)
            self.assertTrue(created)
            incident = Incident.query.filter_by(hazard_type='flood', location='Flood in Batangas').first()
            self.assertIsNotNone(incident)
            self.assertEqual(incident.level, 'High')
            self.assertTrue(incident.alert)
            self.assertIn('GDACS', incident.message)

    def test_monitor_floods_gdacs_does_not_alert_on_green(self):
        parsed_flood = {
            'event_id': 2, 'name': 'Minor Flood', 'country': 'Philippines',
            'alert_level': 'Green', 'severity_text': '', 'from_date': '', 'to_date': '',
            'is_current': True, 'lat': 14.1, 'lon': 121.0, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                scheduler.monitor_floods_gdacs(app)
            incident = Incident.query.filter_by(hazard_type='flood', location='Minor Flood').first()
            self.assertIsNotNone(incident)
            self.assertFalse(incident.alert)
            self.assertEqual(incident.level, 'Moderate')

    def test_monitor_floods_gdacs_skips_duplicate_within_6_hours(self):
        parsed_flood = {
            'event_id': 3, 'name': 'Repeat Flood', 'country': 'Philippines',
            'alert_level': 'Red', 'severity_text': '', 'from_date': '', 'to_date': '',
            'is_current': True, 'lat': 14.1, 'lon': 121.0, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                first = scheduler.monitor_floods_gdacs(app)
                second = scheduler.monitor_floods_gdacs(app)
            self.assertTrue(first)
            self.assertFalse(second)
            count = Incident.query.filter_by(hazard_type='flood', location='Repeat Flood').count()
            self.assertEqual(count, 1)

    def test_monitor_earthquakes_does_not_recreate_incident_once_stale_event_still_in_feed(self):
        """Regression test for the exact bug reported against production: the
        same real earthquake (a single USGS event) kept appearing in the
        "10 most recent" feed for CALABARZON days apart, and each time the
        prior incident's created_at aged past the old 6-hour window, a brand
        new duplicate incident got created for the same physical event.
        De-duping must be keyed on the quake's own USGS event id, not a
        rolling wall-clock window from our own row's created_at."""
        quake = {'event_id': 'us7000example', 'magnitude': 5.2, 'place': '7 km NW of Cabacao, Philippines', 'time': 1723000000000}
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[quake]):
                first = scheduler.monitor_earthquakes(app)
                self.assertTrue(first)

                # Simulate the incident having been created days ago (this is
                # exactly the "created_at aged past the window" condition that
                # used to let the same event slip back through).
                stale = Incident.query.filter_by(hazard_type='earthquake', external_event_id='usgs:us7000example').first()
                self.assertIsNotNone(stale)
                stale.created_at = datetime.utcnow() - timedelta(days=6)
                db.session.commit()

                # USGS still returns the same quake (same id) on a later poll.
                second = scheduler.monitor_earthquakes(app)
            self.assertFalse(second)
            count = Incident.query.filter_by(hazard_type='earthquake', location='7 km NW of Cabacao, Philippines').count()
            self.assertEqual(count, 1)

    def test_monitor_earthquakes_creates_new_incident_for_genuinely_different_event(self):
        """A different USGS event id at a similar location (e.g. an
        aftershock) must still create its own incident -- the fix must not
        become a blanket "one incident per location, ever" suppression."""
        first_quake = {'event_id': 'us7000first', 'magnitude': 5.2, 'place': 'Cabacao, Philippines', 'time': 1723000000000}
        second_quake = {'event_id': 'us7000second', 'magnitude': 4.8, 'place': 'Cabacao, Philippines', 'time': 1723003600000}
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[first_quake]):
                scheduler.monitor_earthquakes(app)
            with patch.object(scheduler, 'get_earthquake_data', return_value=[second_quake]):
                created = scheduler.monitor_earthquakes(app)
            self.assertTrue(created)
            count = Incident.query.filter_by(hazard_type='earthquake', location='Cabacao, Philippines').count()
            self.assertEqual(count, 2)

    def test_monitor_volcanoes_eonet_does_not_recreate_incident_once_stale_event_still_open(self):
        """Same regression as the earthquake test above, for the monitor
        that had not yet been updated to event-id-based de-duping: EONET
        marks an event 'open' for as long as it's tracked (weeks, for
        something like Taal), so the old 6-hour window would re-alert on
        the same open event indefinitely."""
        parsed_volcano = {
            'event_id': 'EONET_9999', 'title': 'Taal Volcano', 'date': '2026-08-07T00:00:00Z',
            'lat': 14.00, 'lon': 120.99, 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/EONET_9999',
            'source': 'NASA EONET',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[parsed_volcano]):
                first = scheduler.monitor_volcanoes_eonet(app)
                self.assertTrue(first)

                stale = Incident.query.filter_by(hazard_type='volcanic', external_event_id='eonet:EONET_9999').first()
                self.assertIsNotNone(stale)
                stale.created_at = datetime.utcnow() - timedelta(days=6)
                db.session.commit()

                second = scheduler.monitor_volcanoes_eonet(app)
            self.assertFalse(second)
            count = Incident.query.filter_by(hazard_type='volcanic', location='Taal Volcano').count()
            self.assertEqual(count, 1)

    def test_monitor_volcanoes_eonet_creates_high_alert_incident(self):
        parsed_volcano = {
            'event_id': 'EONET_1111', 'title': 'Taal Volcano', 'date': '2026-08-11T00:00:00Z',
            'lat': 14.00, 'lon': 120.99, 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1111',
            'source': 'NASA EONET',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[parsed_volcano]):
                created = scheduler.monitor_volcanoes_eonet(app)
            self.assertTrue(created)
            incident = Incident.query.filter_by(hazard_type='volcanic', location='Taal Volcano').first()
            self.assertIsNotNone(incident)
            self.assertEqual(incident.level, 'High')
            self.assertTrue(incident.alert)
            self.assertIn('EONET', incident.message)

    def test_monitor_volcanoes_eonet_no_events_returns_false(self):
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[]):
                created = scheduler.monitor_volcanoes_eonet(app)
            self.assertFalse(created)

    def test_monitor_earthquakes_sets_event_time_from_usgs_epoch_millis(self):
        """This is the field that lets the UI show 'this actually happened
        on Aug 7' instead of only 'we logged this on Aug 13' -- see
        models.Incident.display_time and hazard_macros.event_time_cell."""
        # 2026-08-07T14:22:00 UTC, in epoch milliseconds.
        quake = {'event_id': 'us7000realdate', 'magnitude': 5.2, 'place': 'Cabacao, Philippines', 'time': 1786112520000}
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[quake]):
                scheduler.monitor_earthquakes(app)
            incident = Incident.query.filter_by(external_event_id='usgs:us7000realdate').first()
            self.assertIsNotNone(incident.event_time)
            self.assertEqual(incident.event_time.year, 2026)
            self.assertEqual(incident.event_time.month, 8)
            self.assertEqual(incident.event_time.day, 7)

    def test_monitor_earthquakes_missing_time_leaves_event_time_null_without_erroring(self):
        quake = {'event_id': 'us7000notime', 'magnitude': 5.0, 'place': 'Cabacao, Philippines'}
        with self.app.app_context():
            with patch.object(scheduler, 'get_earthquake_data', return_value=[quake]):
                created = scheduler.monitor_earthquakes(app)
            self.assertTrue(created)
            incident = Incident.query.filter_by(external_event_id='usgs:us7000notime').first()
            self.assertIsNone(incident.event_time)
            # display_time must still fall back cleanly to created_at.
            self.assertEqual(incident.display_time, incident.created_at)

    def test_monitor_floods_gdacs_sets_event_time_from_from_date(self):
        parsed_flood = {
            'event_id': 42, 'name': 'Flood with date', 'country': 'Philippines',
            'alert_level': 'Orange', 'severity_text': '', 'from_date': '2026-08-10T06:30:00',
            'to_date': '', 'is_current': True, 'lat': 14.1, 'lon': 121.0, 'source': 'GDACS',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_flood_events', return_value=[parsed_flood]):
                scheduler.monitor_floods_gdacs(app)
            incident = Incident.query.filter_by(external_event_id='gdacs:42').first()
            self.assertEqual(incident.event_time, datetime(2026, 8, 10, 6, 30))

    def test_monitor_volcanoes_eonet_sets_event_time_from_iso_date_with_z_suffix(self):
        parsed_volcano = {
            'event_id': 'EONET_2222', 'title': 'Mayon Volcano', 'date': '2026-08-01T03:00:00Z',
            'lat': 14.00, 'lon': 120.99, 'link': 'https://eonet.gsfc.nasa.gov/api/v3/events/EONET_2222',
            'source': 'NASA EONET',
        }
        with self.app.app_context():
            with patch.object(scheduler, 'get_volcano_events', return_value=[parsed_volcano]):
                scheduler.monitor_volcanoes_eonet(app)
            incident = Incident.query.filter_by(external_event_id='eonet:EONET_2222').first()
            self.assertEqual(incident.event_time, datetime(2026, 8, 1, 3, 0))

    def test_parse_epoch_millis_handles_none_and_garbage(self):
        self.assertIsNone(scheduler._parse_epoch_millis(None))
        self.assertIsNone(scheduler._parse_epoch_millis('not-a-number'))
        self.assertIsNone(scheduler._parse_epoch_millis(object()))

    def test_parse_iso_datetime_handles_none_and_garbage(self):
        self.assertIsNone(scheduler._parse_iso_datetime(None))
        self.assertIsNone(scheduler._parse_iso_datetime(''))
        self.assertIsNone(scheduler._parse_iso_datetime('not-a-date'))

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
