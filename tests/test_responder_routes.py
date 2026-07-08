import importlib
import os
import unittest
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from flask import render_template_string

import app as app_module
from app import app, db
from models import User, CitizenReport, Incident, IncidentResponse, PostIncidentReport
import scheduler


@app.route('/force-500')
def force_500():
    raise RuntimeError('intentional test failure')


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

    def test_create_default_admin_uses_default_credentials(self):
        with self.app.app_context():
            existing = User(username='admin', email='admin@dics-ai.local', password='legacy', role='user')
            db.session.add(existing)
            db.session.commit()

            app_module.create_default_admin()
            admin = User.query.filter_by(username='admin').first()
            self.assertEqual(admin.role, 'admin')
            self.assertTrue(app_module.check_password_hash(admin.password, 'Admin123!'))

    def test_register_requires_minimum_password_length(self):
        response = self.client.post('/register', data={
            'username': 'newuser',
            'email': 'newuser@example.com',
            'password': 'short',
            'full_name': 'New User',
            'contact_number': '09170000000',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Password must be at least 8 characters.', response.data)

    def test_public_registration_assigns_citizen_role(self):
        response = self.client.post('/register', data={
            'username': 'citizenuser',
            'email': 'citizen@example.com',
            'password': 'strongpass123',
            'full_name': 'Citizen User',
            'contact_number': '09170000000',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            user = User.query.filter_by(username='citizenuser').first()
            self.assertIsNotNone(user)
            self.assertEqual(user.role, 'citizen')

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

    def test_secret_key_falls_back_to_default_when_unset(self):
        original_secret = os.environ.get('SECRET_KEY')
        os.environ.pop('SECRET_KEY', None)

        try:
            import app as app_module
            app_module = importlib.reload(app_module)
            self.assertTrue(app_module.app.config['SECRET_KEY'])
            self.assertEqual(app_module.app.config['SECRET_KEY'], 'dev-secret-key-change-me')
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

        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'citizen'

        response = self.client.get('/api/map-pins')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(isinstance(data, list))
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]['hazard_type'], 'flood')
        self.assertEqual(data[0]['lat'], 14.1234)
        self.assertEqual(data[0]['lng'], 121.5678)

    def test_custom_error_handlers_render_friendly_pages(self):
        response = self.client.get('/does-not-exist')
        self.assertEqual(response.status_code, 404)
        self.assertIn(b'Page Not Found', response.data)
        self.assertIn(b'The page you requested could not be found.', response.data)

        response = self.client.get('/force-500')
        self.assertEqual(response.status_code, 500)
        self.assertIn(b'Something went wrong on our side.', response.data)

    def test_template_rendering_without_request_context_is_safe(self):
        with self.app.app_context():
            rendered = render_template_string('Status: {{ alert_count }}', alert_count=0)

        self.assertEqual(rendered, 'Status: 0')

    def test_monitor_hazards_creates_incident_for_high_risk_prediction(self):
        weather_data = {
            'city': 'Lipa',
            'temperature': 31,
            'humidity': 85,
            'pressure': 1008,
            'wind_speed': 8,
            'rainfall': 20,
            'weather': 'heavy rain',
            'fetched_at': 'now',
        }
        prediction = {
            'type': 'flood',
            'score': 80.0,
            'level': 'Severe',
            'message': 'Severe hazard risk.',
            'alert': True,
        }

        with patch.object(scheduler, 'get_weather_data', return_value=weather_data), \
             patch.object(scheduler, 'predict_hazard', return_value=prediction):
            with self.app.app_context():
                scheduler.monitor_hazards()

        with self.app.app_context():
            incident = Incident.query.filter_by(hazard_type='flood').order_by(Incident.created_at.desc()).first()
            self.assertIsNotNone(incident)
            self.assertTrue(incident.alert)
            self.assertEqual(incident.score, 80.0)
            self.assertEqual(incident.location, 'Lipa')

    def test_monitor_hazards_creates_incidents_for_multiple_hazard_types(self):
        weather_data = {
            'city': 'Lipa',
            'temperature': 31,
            'humidity': 85,
            'pressure': 1008,
            'wind_speed': 8,
            'rainfall': 20,
            'weather': 'heavy rain',
            'fetched_at': 'now',
        }

        def fake_predict_hazard(hazard_type, **kwargs):
            return {
                'type': hazard_type,
                'score': 80.0,
                'level': 'Severe',
                'message': f'Severe {hazard_type} risk.',
                'alert': True,
            }

        with patch.object(scheduler, 'get_weather_data', return_value=weather_data), \
             patch.object(scheduler, 'predict_hazard', side_effect=fake_predict_hazard):
            with self.app.app_context():
                scheduler.monitor_hazards()

        with self.app.app_context():
            incidents = Incident.query.filter(Incident.hazard_type.in_(['flood', 'landslide'])).all()
            self.assertEqual(len(incidents), 2)
            self.assertEqual({incident.hazard_type for incident in incidents}, {'flood', 'landslide'})

    def test_post_incident_evaluation_saves_report_for_closed_response(self):
        with self.app.app_context():
            commander = User(
                username='commander1',
                email='commander@example.com',
                password='secret',
                role='incident_commander',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(commander)
            db.session.commit()

            incident = Incident(
                user_id=commander.id,
                hazard_type='flood',
                location='Lipa',
                message='Flooding reported',
                level='HIGH',
                alert=True,
                status='CLOSED',
                reported_by='system',
            )
            db.session.add(incident)
            db.session.commit()

            response = IncidentResponse(
                incident_id=incident.id,
                commander_id=commander.id,
                status='CLOSED',
                situation_summary='Resolved',
            )
            db.session.add(response)
            db.session.commit()
            db.session.refresh(response)

        with self.client.session_transaction() as session:
            session['username'] = 'commander1'
            session['role'] = 'incident_commander'
            session['agency'] = 'BFP'

        response_result = self.client.post(f'/incident-response/{response.id}/post-incident-evaluation', data={
            'lessons_learned': 'Improved shelter coordination',
            'response_rating': '5',
            'recommendations': 'Add more evacuation buses',
        }, follow_redirects=True)

        self.assertEqual(response_result.status_code, 200)
        with self.app.app_context():
            report = PostIncidentReport.query.filter_by(incident_response_id=response.id).first()
            self.assertIsNotNone(report)
            self.assertEqual(report.lessons_learned, 'Improved shelter coordination')
            self.assertEqual(report.response_rating, 5)
            self.assertEqual(report.recommendations, 'Add more evacuation buses')

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
            from models import IncidentMessage
            message = IncidentMessage.query.filter_by(title='Test Broadcast').first()
            self.assertIsNotNone(message)
            self.assertEqual(message.content, 'This is a test broadcast message.')
            self.assertEqual(message.reporter_id, coordinator_id)
            self.assertEqual(message.incident_response_id, incident_response_id)
            self.assertEqual(message.source, 'coordinator')


if __name__ == '__main__':
    unittest.main()
