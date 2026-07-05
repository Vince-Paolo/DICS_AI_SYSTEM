import importlib
import os
import unittest
from io import BytesIO

from app import app, db
from models import User, CitizenReport, Incident


class ResponderRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                username='responder1',
                email='responder@example.com',
                password='secret',
                role='field_responder',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(user)
            db.session.commit()

    def test_field_responder_dashboard_requires_login(self):
        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 302)

    def test_field_responder_dashboard_renders_for_role(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'field_responder'
            session['agency'] = 'BFP'

        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Field Responder', response.data)

    def test_secret_key_uses_environment_and_initializes_db_on_request(self):
        original_secret = os.environ.get('SECRET_KEY')
        os.environ['SECRET_KEY'] = 'env-secret-test'

        try:
            import app as app_module
            app_module = importlib.reload(app_module)
            client = app_module.app.test_client()

            response = client.get('/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(app_module.app.config['SECRET_KEY'], 'env-secret-test')
            self.assertTrue(app_module._init_attempted)
        finally:
            if original_secret is None:
                os.environ.pop('SECRET_KEY', None)
            else:
                os.environ['SECRET_KEY'] = original_secret

    def test_citizen_report_creates_record_with_photo_and_anonymous_flag(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        response = self.client.post('/citizen-report', data={
            'hazard_type': 'flood',
            'severity': 'high',
            'location': 'Barangay Test',
            'description': 'Water rising',
            'affected_people': '5',
            'injuries': '0',
            'contact': '09171234567',
            'gps_lat': '14.1234',
            'gps_lng': '121.5678',
            'anonymous': 'on',
            'photo': (BytesIO(b'photo-data'), 'photo.jpg'),
        }, content_type='multipart/form-data', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            report = CitizenReport.query.filter_by(location='Barangay Test').first()
            self.assertIsNotNone(report)
            self.assertTrue(report.anonymous)
            self.assertEqual(report.gps_latitude, 14.1234)
            self.assertEqual(report.gps_longitude, 121.5678)
            self.assertIsNotNone(report.photo_filename)
            upload_response = self.client.get(f'/uploads/{report.photo_filename}')
            self.assertEqual(upload_response.status_code, 200)
            self.assertIn(b'photo-data', upload_response.data)

    def test_map_pins_endpoint_returns_active_incidents_with_coordinates(self):
        with self.app.app_context():
            incident = Incident(
                user_id=1,
                hazard_type='flood',
                location='Barangay Test',
                message='Water rising',
                level='high',
                alert=True,
                status='ACTIVE',
                reported_by='citizen',
            )
            db.session.add(incident)
            db.session.commit()

            citizen_report = CitizenReport(
                user_id=1,
                hazard_type='flood',
                severity='high',
                location='Barangay Test',
                description='Water rising',
                gps_latitude=14.1234,
                gps_longitude=121.5678,
                anonymous=False,
            )
            db.session.add(citizen_report)
            db.session.commit()

        response = self.client.get('/api/map-pins')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(isinstance(data, list))
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]['hazard_type'], 'flood')
        self.assertEqual(data[0]['lat'], 14.1234)
        self.assertEqual(data[0]['lng'], 121.5678)

    def test_coordinator_comms_page_renders_for_agency_coordinator(self):
        with self.client.session_transaction() as session:
            session['username'] = 'coordinator1'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'DILG'

        with self.app.app_context():
            coordinator = User(
                username='coordinator1',
                email='coordinator@example.com',
                password='secret',
                role='agency_coordinator',
                agency='DILG',
                email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()

            incident = Incident(
                user_id=coordinator.id,
                hazard_type='storm',
                location='Region Test',
                message='Storm forming',
                level='moderate',
                alert=False,
                status='ACTIVE',
                reported_by='system',
            )
            db.session.add(incident)
            db.session.commit()

            from models import IncidentResponse
            response = IncidentResponse(
                incident_id=incident.id,
                commander_id=coordinator.id,
                status='ACTIVE',
                situation_summary='Summary',
            )
            db.session.add(response)
            db.session.commit()

        response = self.client.get('/coordinator/comms')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Communication Center', response.data)

    def test_coordinator_submit_report_creates_message(self):
        with self.client.session_transaction() as session:
            session['username'] = 'coordinator1'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'DILG'

        with self.app.app_context():
            coordinator = User(
                username='coordinator1',
                email='coordinator@example.com',
                password='secret',
                role='agency_coordinator',
                agency='DILG',
                email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()
            coordinator_id = coordinator.id

            incident = Incident(
                user_id=coordinator_id,
                hazard_type='storm',
                location='Region Test',
                message='Storm forming',
                level='moderate',
                alert=False,
                status='ACTIVE',
                reported_by='system',
            )
            db.session.add(incident)
            db.session.commit()

            from models import IncidentResponse
            incident_response = IncidentResponse(
                incident_id=incident.id,
                commander_id=coordinator_id,
                status='ACTIVE',
                situation_summary='Summary',
            )
            db.session.add(incident_response)
            db.session.commit()
            incident_response_id = incident_response.id

        response = self.client.post('/coordinator/reports/submit', data={
            'response_id': incident_response_id,
            'title': 'Test Broadcast',
            'content': 'This is a test broadcast message.',
            'report_type': 'UPDATE',
            'affected_areas': 'Region Test',
            'evacuated': '0',
            'casualties': '0',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            from models import Message
            message = Message.query.filter_by(title='Test Broadcast').first()
            self.assertIsNotNone(message)
            self.assertEqual(message.content, 'This is a test broadcast message.')
            self.assertEqual(message.sender_id, coordinator_id)
            self.assertEqual(message.incident_response_id, incident_response_id)


if __name__ == '__main__':
    unittest.main()
