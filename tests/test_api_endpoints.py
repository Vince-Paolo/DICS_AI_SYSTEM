import os
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_api_endpoints.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from app import app, db
from models import Barangay, CitizenReport, EvacuationCenter, Facility, Incident, IncidentResponse, Municipality, Province, Resource, Task, User
from seed.demo_data import seed_geography_data


class ApiEndpointFunctionalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        # These tests cover the legacy citizen report form / SOS, which are
        # retired by default (see tests/test_emergency_assistance.py).
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, PUBLIC_REPORTING_ENABLED=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            seed_geography_data()
            db.session.add_all([
                User(
                    username='api_citizen',
                    email='api-citizen@example.com',
                    password='secret',
                    role='citizen',
                    email_verified=True,
                ),
                User(
                    username='api_commander',
                    email='api-commander@example.com',
                    password='secret',
                    role='incident_commander',
                    email_verified=True,
                ),
                User(
                    username='api_coordinator',
                    email='api-coordinator@example.com',
                    password='secret',
                    role='agency_coordinator',
                    agency='BFP',
                    email_verified=True,
                ),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def _login(self, username, role):
        with self.client.session_transaction() as session:
            session['username'] = username
            session['role'] = role

    def test_municipalities_api_returns_sorted_records_for_a_province(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            province_id = 1

        response = self.client.get(f'/api/municipalities/{province_id}')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('municipalities', payload)
        names = [item['name'] for item in payload['municipalities']]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(set(item) == {'id', 'name'} for item in payload['municipalities']))

    def test_hazard_map_province_dropdown_lists_real_provinces(self):
        """The province filter used to only ever show whatever provinces
        happened to be attached to currently-plotted incidents, so with few
        or no incidents on the map it rendered as good as empty. It should
        always list the real, seeded provinces regardless of what's
        currently plotted."""
        self._login('api_citizen', 'citizen')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        with self.app.app_context():
            province_names = [p.name for p in Province.query.all()]
        self.assertTrue(len(province_names) > 0)
        for name in province_names:
            self.assertIn(f'>{name}<', html)

    def test_hazard_map_search_bar_is_rendered_for_client_filtering(self):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn('id="hazardSearchInput"', html)
        self.assertIn('Search hazard areas...', html)

    def test_hazard_map_rainfall_and_typhoon_tracking_apis_return_data(self):
        self._login('api_citizen', 'citizen')

        rainfall_response = self.client.get('/api/hazard-layers/rainfall')
        typhoon_response = self.client.get('/api/hazard-layers/typhoon-tracks')

        self.assertEqual(rainfall_response.status_code, 200)
        self.assertEqual(typhoon_response.status_code, 200)

        rainfall = rainfall_response.get_json()
        typhoon = typhoon_response.get_json()

        self.assertIsInstance(rainfall, list)
        self.assertIsInstance(typhoon, list)
        self.assertGreater(len(rainfall), 0)
        self.assertGreater(len(typhoon), 0)

        self.assertIn('lat', rainfall[0])
        self.assertIn('lon', rainfall[0])
        self.assertIn('track', typhoon[0])

    def test_barangays_api_returns_sorted_records_for_a_municipality(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            municipality_id = 1

        response = self.client.get(f'/api/barangays/{municipality_id}')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('barangays', payload)
        names = [item['name'] for item in payload['barangays']]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(set(item) == {'id', 'name'} for item in payload['barangays']))

    @patch('app.get_earthquake_data', return_value=[{'magnitude': 4.2, 'place': 'Test'}])
    @patch('app.get_all_weather_data', return_value={'Cavite': {'rainfall': 12}})
    def test_realtime_data_api_returns_weather_and_earthquake_payloads(self, mock_weather, mock_earthquakes):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/api/realtime-data')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'weather': {'Cavite': {'rainfall': 12}},
            'earthquakes': [{'magnitude': 4.2, 'place': 'Test'}],
        })
        mock_weather.assert_called_once_with()
        mock_earthquakes.assert_called_once_with()

    @patch('app.get_earthquake_data', return_value=[{'magnitude': 5.1}])
    def test_dashboard_stats_api_reports_only_the_logged_in_users_incidents(self, mock_earthquakes):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            db.session.add(Incident(
                user_id=user.id,
                hazard_type='flood',
                message='Test incident',
                score=62.5,
                alert=True,
                status='ACTIVE',
            ))
            db.session.add(Incident(
                user_id=User.query.filter_by(username='api_commander').one().id,
                hazard_type='fire',
                message='Other incident',
                score=99,
                alert=True,
                status='ACTIVE',
            ))
            db.session.commit()

        response = self.client.get('/api/dashboard-stats')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'alert_count': 1,
            'total_incidents': 1,
            'latest_risk_score': 62.5,
            'latest_earthquake_magnitude': 5.1,
        })
        mock_earthquakes.assert_called_once_with()

    def test_map_pins_api_returns_only_active_geolocated_incidents(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            report = user.citizen_reports[0] if user.citizen_reports else None
            if report is None:
                from models import CitizenReport
                report = CitizenReport(
                    user_id=user.id,
                    hazard_type='flood',
                    severity='HIGH',
                    location='Cavite',
                    description='Mapped flooding',
                    gps_latitude=14.28,
                    gps_longitude=120.88,
                )
                report.province_id = Province.query.first().id
                report.municipality_id = Municipality.query.filter_by(province_id=report.province_id).first().id
                db.session.add(report)
                db.session.flush()
            db.session.add(Incident(
                user_id=user.id,
                citizen_report_id=report.id,
                hazard_type='flood',
                location='Cavite',
                message='Mapped flooding',
                level='Severe',
                alert=False,
                status='ACTIVE',
                reported_by='citizen',
            ))
            db.session.add(Incident(
                user_id=user.id,
                hazard_type='fire',
                location='No coordinates',
                message='Not mapped',
                alert=True,
                status='ACTIVE',
            ))
            db.session.commit()

        response = self.client.get('/api/map-pins')

        self.assertEqual(response.status_code, 200)
        pins = response.get_json()
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]['hazard_type'], 'flood')
        self.assertEqual(pins[0]['lat'], 14.28)
        self.assertEqual(pins[0]['lng'], 120.88)
        self.assertEqual(pins[0]['level'], 'Severe')
        self.assertIsNotNone(pins[0]['province'])
        self.assertIsNotNone(pins[0]['municipality'])
        self.assertEqual(pins[0]['status'], 'ACTIVE')

    def test_emergency_sos_incident_appears_on_hazard_map_with_coordinates(self):
        self._login('api_citizen', 'citizen')

        response = self.client.post('/emergency-sos', json={
            'location': 'Barangay Uno',
            'latitude': 14.32,
            'longitude': 120.9,
        })

        self.assertEqual(response.status_code, 200)
        pins = self.client.get('/api/map-pins').get_json()
        self.assertTrue(any(
            pin.get('hazard_type') == 'EMERGENCY' and pin.get('lat') == 14.32 and pin.get('lng') == 120.9
            for pin in pins
        ))

    def test_login_and_logout_responses_disable_browser_cache(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

        with self.client.session_transaction() as session:
            session['username'] = 'api_citizen'
            session['role'] = 'citizen'

        response = self.client.get('/login')
        self.assertEqual(response.status_code, 302)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

        response = self.client.get('/logout', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

    def test_file_storage_raises_when_s3_region_is_placeholder(self):
        from services.file_storage import FileStorage

        app_stub = type('AppStub', (), {
            'config': {
                'FILE_STORAGE_BACKEND': 's3',
                'FILE_STORAGE_BUCKET': 'demo-bucket',
                'FILE_STORAGE_PREFIX': 'uploads',
                'FILE_STORAGE_REGION': '...',
                'FILE_STORAGE_ENDPOINT_URL': 'https://s3.example.com',
                'FILE_STORAGE_ACCESS_KEY_ID': 'demo-key',
                'FILE_STORAGE_SECRET_ACCESS_KEY': 'demo-secret',
                'UPLOAD_FOLDER': os.path.join('instance', 'uploads', 'citizen_reports'),
            }
        })()

        with self.assertRaises(RuntimeError):
            FileStorage(app_stub)

    def test_legacy_sqlite_database_adds_missing_resource_request_id_column(self):
        from app import app, migrate_legacy_sqlite_resource_columns

        configured_uri = app.config['SQLALCHEMY_DATABASE_URI']
        self.assertTrue(configured_uri.startswith('sqlite:'))

        db_path = configured_uri.removeprefix('sqlite:///')
        if os.name == 'nt' and db_path.startswith('/'):
            db_path = db_path[1:]
        db_path = os.path.abspath(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.execute('DROP TABLE IF EXISTS resource')
            conn.execute('''
                CREATE TABLE resource (
                    id INTEGER PRIMARY KEY,
                    incident_response_id INTEGER NOT NULL,
                    resource_type VARCHAR(100) NOT NULL,
                    agency VARCHAR(150) NOT NULL,
                    quantity INTEGER NOT NULL,
                    status VARCHAR(20) DEFAULT 'AVAILABLE',
                    location VARCHAR(255),
                    notes TEXT,
                    allocated_at DATETIME,
                    deployed_at DATETIME
                )
            ''')
            conn.commit()

        migrate_legacy_sqlite_resource_columns()

        with sqlite3.connect(db_path) as conn:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(resource)')]
        self.assertIn('resource_request_id', columns)
        self.assertIn('updated_at', columns)

    def test_map_operational_layer_endpoints_return_geolocated_centers_and_resources(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            report = CitizenReport(
                user_id=user.id,
                hazard_type='flood',
                severity='HIGH',
                location='Cavite',
                description='Response location',
                gps_latitude=14.28,
                gps_longitude=120.88,
            )
            db.session.add(report)
            db.session.flush()
            incident = Incident(
                user_id=user.id,
                citizen_report_id=report.id,
                hazard_type='flood',
                location='Cavite',
                message='Response location',
                level='High',
                status='ACTIVE',
            )
            db.session.add(incident)
            db.session.flush()
            response = IncidentResponse(incident_id=incident.id, commander_id=user.id, status='ACTIVE')
            facility = Facility(name='Mapped Shelter', facility_type='Evacuation Center', latitude=14.3, longitude=120.9)
            db.session.add_all([response, facility])
            db.session.flush()
            db.session.add(EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=20, status='OPEN'))
            db.session.add(Resource(incident_response_id=response.id, resource_type='Water', agency='BFP', quantity=5, status='DEPLOYED'))
            db.session.commit()

        centers = self.client.get('/api/map-evacuation-centers')
        resources = self.client.get('/api/map-resources')

        self.assertEqual(centers.status_code, 200)
        self.assertEqual(resources.status_code, 200)
        self.assertEqual(centers.get_json()[0]['name'], 'Mapped Shelter')
        self.assertEqual(resources.get_json()[0]['resource_type'], 'Water')

    def test_analytics_api_returns_operational_summary_for_coordinator(self):
        self._login('api_coordinator', 'agency_coordinator')

        response = self.client.get('/api/analytics-data')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['incident_counts'], {})
        self.assertEqual(payload['response_time']['total_resolved'], 0)
        self.assertEqual(payload['response_time']['average_minutes'], 0)
        self.assertIn('resource_utilization', payload)
        self.assertEqual(payload['resource_utilization']['status_counts'], {})
        self.assertEqual(payload['resource_utilization']['type_counts'], {})

    def test_analytics_api_includes_monthly_and_geography_breakdowns(self):
        self._login('api_coordinator', 'agency_coordinator')
        with self.app.app_context():
            municipality = Municipality.query.first()
            barangay = Barangay.query.filter_by(municipality_id=municipality.id).first()
            municipality_name = municipality.name
            barangay_name = barangay.name
            other_municipality = Municipality.query.filter(Municipality.id != municipality.id).first()
            other_barangay = Barangay.query.filter_by(municipality_id=other_municipality.id).first()
            db.session.add_all([
                Incident(
                    hazard_type='flood',
                    location='Flooded barangay',
                    message='Flooding in same barangay',
                    level='Severe',
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    status='ACTIVE',
                    created_at=datetime(2026, 2, 5),
                ),
                Incident(
                    hazard_type='flood',
                    location='Flooded barangay again',
                    message='Flooding repeated',
                    level='High',
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    status='VERIFIED',
                    created_at=datetime(2026, 2, 17),
                ),
                Incident(
                    hazard_type='fire',
                    location='Fire elsewhere',
                    message='Warehouse fire',
                    level='High',
                    municipality_id=other_municipality.id,
                    barangay_id=other_barangay.id,
                    status='ACTIVE',
                    created_at=datetime(2026, 2, 18),
                ),
            ])
            db.session.commit()

        response = self.client.get('/api/analytics-data?year=2026&month=2')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreaterEqual(len(payload['monthly_counts']), 1)
        self.assertTrue(any(item['month'] == '2026-02' and item['count'] >= 2 for item in payload['monthly_counts']))
        self.assertTrue(any(
            item['hazard_type'] == 'flood' and item['municipality'] == municipality_name and item['barangay'] == barangay_name and item['count'] == 2
            for item in payload['geo_counts']
        ))

        export_response = self.client.get('/api/analytics-export?format=csv&year=2026&month=2')
        self.assertEqual(export_response.status_code, 200)
        self.assertIn('hazard_type', export_response.get_data(as_text=True))

    def test_incident_response_stats_api_returns_commander_counts(self):
        self._login('api_commander', 'incident_commander')
        with self.app.app_context():
            commander = User.query.filter_by(username='api_commander').one()
            incident = Incident(hazard_type='flood', message='Response test', status='ACTIVE')
            db.session.add(incident)
            db.session.flush()
            response = IncidentResponse(
                incident_id=incident.id,
                commander_id=commander.id,
                status='ACTIVE',
            )
            db.session.add(response)
            db.session.flush()
            db.session.add(Task(
                incident_response_id=response.id,
                assigned_to_agency='BFP',
                assigned_by_id=commander.id,
                status='PENDING',
                title='Task',
                description='Test task',
            ))
            db.session.add(Resource(
                incident_response_id=response.id,
                agency='BFP',
                status='DEPLOYED',
                quantity=3,
                resource_type='Water',
            ))
            db.session.commit()

        result = self.client.get('/api/incident-response-stats')

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), {
            'active_responses': 1,
            'pending_tasks': 1,
            'deployed_resources': 1,
        })


if __name__ == '__main__':
    unittest.main()
