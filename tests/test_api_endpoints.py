import os
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_api_endpoints.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from app import app, db
from models import CitizenReport, EvacuationCenter, Facility, Incident, IncidentResponse, Municipality, Province, Resource, Task, User
from seed.demo_data import seed_geography_data


class ApiEndpointFunctionalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
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
