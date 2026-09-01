import importlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.security import check_password_hash

from PIL import Image

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
# CRITICAL: this must be set before `app` is imported. Flask-SQLAlchemy binds
# and caches its engine the first time it's used; overriding
# SQLALCHEMY_DATABASE_URI on the config *after* import is not reliable and
# has previously caused the test suite to create/drop tables against the
# real instance/database.db file instead of an isolated database. Use a
# file-backed test DB so the schema is stable and reproducible before import.
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_responder_routes.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from flask import render_template_string

import app as app_module
from app import app, db, create_default_admin
from models import User, CitizenReport, Incident, IncidentResponse, PostIncidentReport, Task, Resource, IncidentMessage, Province, Municipality, Barangay
from seed.demo_data import seed_geography_data
import scheduler


@app.route('/force-500')
def force_500():
    raise RuntimeError('intentional test failure')


class ResponderRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

        # Flask-Limiter's storage is a module-level singleton, not reset by
        # db.drop_all()/create_all() below -- without this, a rate-limit
        # test earlier in the suite (or run order changing) would leave
        # quota partially consumed for any other test hitting the same
        # route, causing flaky failures unrelated to what that test
        # actually checks.
        from app import limiter
        limiter.reset()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            # `app.py`'s lazy_init() only seeds geography/agency reference data once
            # per process (guarded by a module-level flag), so it won't re-fire after
            # this test's drop_all/create_all wipes the tables. Re-seed explicitly so
            # every test gets real barangay/municipality/province rows to reference,
            # regardless of run order.
            seed_geography_data()
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

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def test_predict_hazard_fallback_contract_uses_insufficient_data(self):
        from ai import decision_support

        # Inputs deliberately chosen to clear every `_deterministic_low_risk_exit`
        # threshold (rainfall/river/humidity/population all "high"), so this
        # actually reaches the AI adapter call instead of short-circuiting to the
        # deterministic Low-risk result -- otherwise this test never exercises the
        # "provider unavailable" fallback contract it's meant to verify.
        with patch('ai.decision_support.AI_PROVIDER', 'anthropic'), \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}, clear=False):
            prediction = decision_support.predict_hazard(
                'flood',
                rainfall_mm=200,
                river_level_m=5.0,
                humidity_pct=95,
                population_density=5000,
            )

        self.assertEqual(prediction.get('level'), 'INSUFFICIENT_DATA')
        self.assertTrue(prediction.get('degraded'))
        self.assertFalse(prediction.get('alert'))
        self.assertEqual(prediction.get('score'), 0.0)

    def test_predict_hazard_deterministic_exit_skips_ai_call_for_low_risk_inputs(self):
        from ai import decision_support

        with patch('ai.decision_support.AI_PROVIDER', 'anthropic'), \
             patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'unused-key'}, clear=False), \
             patch.object(decision_support, '_ADAPTERS', {}) as adapters:
            prediction = decision_support.predict_hazard(
                'flood',
                rainfall_mm=0,
                river_level_m=None,
                humidity_pct=0,
                population_density=0,
            )

        self.assertEqual(prediction.get('level'), 'Low')
        self.assertEqual(prediction.get('provider'), 'deterministic')
        self.assertFalse(prediction.get('degraded'))
        self.assertEqual(adapters, {})  # sanity check the patch took effect; adapter was never called

    def test_field_responder_dashboard_requires_login(self):
        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 302)

    def test_forgot_password_flow_generates_reset_token_and_updates_password(self):
        response = self.client.post('/forgot-password', data={'email': 'responder@example.com'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'If an account exists', response.data)

        with self.app.app_context():
            user = User.query.filter_by(email='responder@example.com').first()
            self.assertIsNotNone(user)
            self.assertIsNotNone(user.reset_token)
            self.assertIsNotNone(user.reset_token_expires_at)
            self.assertGreater(user.reset_token_expires_at, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=59))
            token = user.reset_token

        response = self.client.post(
            f'/reset-password/{token}',
            data={'new_password': 'NewSecurePass123', 'confirm_password': 'NewSecurePass123'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Password updated successfully', response.data)

        with self.app.app_context():
            user = User.query.filter_by(email='responder@example.com').first()
            self.assertTrue(check_password_hash(user.password, 'NewSecurePass123'))
            self.assertIsNone(user.reset_token)
            self.assertIsNone(user.reset_token_expires_at)

    def test_forgot_password_sends_reset_email_with_link(self):
        self.app.config.update(TESTING=False, MAIL_SUPPRESS_SEND=True)

        with self.app.app_context():
            from app import mail

            with mail.record_messages() as outbox:
                response = self.client.post('/forgot-password', data={'email': 'responder@example.com'}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'If an account exists', response.data)
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0].recipients, ['responder@example.com'])
        self.assertIn('Reset your password', outbox[0].subject)

        with self.app.app_context():
            user = User.query.filter_by(email='responder@example.com').first()
            self.assertIsNotNone(user.reset_token)
            self.assertIn(f'/reset-password/{user.reset_token}', outbox[0].body)

    def test_shared_layout_exposes_accessibility_landmarks(self):
        response = self.client.get('/')

        self.assertIn(b'href="#main-content"', response.data)
        self.assertIn(b'id="main-content"', response.data)
        self.assertIn(b'id="pageStatus"', response.data)

    def test_security_headers_are_applied_globally(self):
        response = self.client.get('/')

        self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
        self.assertIn("object-src 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Strict-Transport-Security', response.headers)

    def test_hsts_is_added_only_when_enabled_for_https(self):
        self.app.config['ENABLE_HSTS'] = True
        response = self.client.get('/', base_url='https://localhost')

        self.assertEqual(
            response.headers['Strict-Transport-Security'],
            'max-age=31536000; includeSubDomains',
        )

    def test_coordinator_update_task_rejects_unowned_task(self):
        with self.app.app_context():
            coordinator = User(
                username='coordinator1',
                email='coord@example.com',
                password='secret',
                role='agency_coordinator',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()

            incident = Incident(user_id=coordinator.id, hazard_type='earthquake', location='Test', message='Test', level='high', alert=True, status='ACTIVE')
            db.session.add(incident)
            db.session.commit()

            response = IncidentResponse(incident_id=incident.id, commander_id=coordinator.id, status='ACTIVE')
            db.session.add(response)
            db.session.commit()

            task = Task(
                incident_response_id=response.id,
                assigned_to_agency='DOH',
                assigned_by_id=coordinator.id,
                title='Unknown agency task',
                description='Should not be mutable by BFP coordinator',
                status='PENDING',
            )
            db.session.add(task)
            db.session.commit()
            task_id = task.id

        with self.client.session_transaction() as session:
            session['username'] = 'coordinator1'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        response = self.client.post(f'/coordinator/tasks/{task_id}/update', data={'status': 'IN_PROGRESS'}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            refreshed = db.session.get(Task, task_id)
            self.assertEqual(refreshed.status, 'PENDING')

    def test_coordinator_allocate_resource_uses_coordinator_agency(self):
        with self.app.app_context():
            coordinator = User(
                username='coordinator2',
                email='coord2@example.com',
                password='secret',
                role='agency_coordinator',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()

            incident = Incident(user_id=coordinator.id, hazard_type='earthquake', location='Test', message='Test', level='high', alert=True, status='ACTIVE')
            db.session.add(incident)
            db.session.commit()

            response = IncidentResponse(incident_id=incident.id, commander_id=coordinator.id, status='ACTIVE')
            db.session.add(response)
            db.session.commit()

            task = Task(
                incident_response_id=response.id,
                assigned_to_agency='BFP',
                assigned_by_id=coordinator.id,
                title='BFP support task',
                description='Ensures BFP is attached to the response',
                status='PENDING',
            )
            db.session.add(task)
            db.session.commit()
            response_id = response.id

        with self.client.session_transaction() as session:
            session['username'] = 'coordinator2'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        response = self.client.post('/coordinator/resources/allocate', data={
            'response_id': response_id,
            'agency': 'DOH',
            'resource_type': 'Vehicles',
            'quantity': 2,
            'status': 'AVAILABLE',
            'location': 'Base',
            'notes': 'Test',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            resource = Resource.query.filter_by(incident_response_id=response_id).first()
            self.assertIsNotNone(resource)
            self.assertEqual(resource.agency, 'BFP')

    def test_coordinator_submit_report_requires_agency_owned_response_assets(self):
        with self.app.app_context():
            coordinator = User(
                username='coordinator3',
                email='coord3@example.com',
                password='secret',
                role='agency_coordinator',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()

            incident = Incident(user_id=coordinator.id, hazard_type='earthquake', location='Test', message='Test', level='high', alert=True, status='ACTIVE')
            db.session.add(incident)
            db.session.commit()

            response = IncidentResponse(incident_id=incident.id, commander_id=coordinator.id, status='ACTIVE')
            db.session.add(response)
            db.session.commit()
            response_id = response.id

        with self.client.session_transaction() as session:
            session['username'] = 'coordinator3'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        response = self.client.post('/coordinator/reports/submit', data={
            'response_id': response_id,
            'title': 'Test report',
            'content': 'This should be blocked',
            'report_type': 'UPDATE',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(IncidentMessage.query.count(), 0)

    def test_coordinator_response_detail_rejects_unowned_response(self):
        with self.app.app_context():
            coordinator = User(
                username='coordinator4',
                email='coord4@example.com',
                password='secret',
                role='agency_coordinator',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(coordinator)
            other_coordinator = User(
                username='coordinator_other',
                email='coord_other@example.com',
                password='secret',
                role='agency_coordinator',
                agency='DOH',
                email_verified=True,
            )
            db.session.add(other_coordinator)
            db.session.commit()

            incident = Incident(user_id=other_coordinator.id, hazard_type='earthquake', location='Test', message='Test', level='high', alert=True, status='ACTIVE')
            db.session.add(incident)
            db.session.commit()

            response = IncidentResponse(incident_id=incident.id, commander_id=other_coordinator.id, status='ACTIVE')
            db.session.add(response)
            db.session.commit()
            response_id = response.id

        with self.client.session_transaction() as session:
            session['username'] = 'coordinator4'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        response = self.client.get(f'/coordinator/response/{response_id}', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Access denied.', response.data)

    def test_create_default_admin_requires_password_env(self):
        with self.app.app_context():
            os.environ.pop('ADMIN_PASSWORD', None)
            with self.assertRaises(RuntimeError):
                app_module.create_default_admin()

    def test_create_default_admin_uses_default_credentials(self):
        with self.app.app_context():
            existing = User(username='admin', email='admin@dics-ai.local', password='legacy', role='user')
            db.session.add(existing)
            db.session.commit()

            os.environ['ADMIN_PASSWORD'] = 'test-admin-password'
            app_module.create_default_admin()
            admin = User.query.filter_by(username='admin').first()
            self.assertEqual(admin.role, 'admin')
            self.assertTrue(app_module.check_password_hash(admin.password, 'test-admin-password'))

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

    def test_responder_dashboard_rejects_stale_authenticated_session(self):
        with self.client.session_transaction() as session:
            session['username'] = 'missing-responder'
            session['role'] = 'field_responder'

        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')

    def test_permission_policies_use_supplied_user(self):
        from services import permissions

        citizen = SimpleNamespace(id=11, role='citizen')
        commander = SimpleNamespace(id=12, role='incident_commander')
        incident = SimpleNamespace(user_id=citizen.id)

        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'citizen'

        self.assertTrue(permissions.can_view_incident(citizen, incident))
        self.assertTrue(permissions.can_view_incident(commander, incident))
        self.assertTrue(permissions.can_issue_alert(commander))
        self.assertFalse(permissions.can_manage_users(citizen))

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

    def test_secret_key_generates_random_value_when_unset(self):
        # SECRET_KEY must never fall back to a fixed, known string checked into
        # source control (session forgery risk). When unset, the app should
        # generate a random per-process key instead.
        original_secret = os.environ.get('SECRET_KEY')
        os.environ.pop('SECRET_KEY', None)

        try:
            import app as app_module
            with self.assertWarns(RuntimeWarning):
                app_module = importlib.reload(app_module)
            secret_key = app_module.app.config['SECRET_KEY']
            self.assertTrue(secret_key)
            self.assertNotEqual(secret_key, 'dev-secret-key-change-me')
            self.assertGreaterEqual(len(secret_key), 32)
        finally:
            if original_secret is None:
                os.environ.pop('SECRET_KEY', None)
            else:
                os.environ['SECRET_KEY'] = original_secret
            importlib.reload(app_module)

    def test_citizen_report_creates_record_with_photo_and_anonymous_flag(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        image_stream = BytesIO()
        Image.new('RGB', (1, 1), color='white').save(image_stream, format='JPEG')
        image_stream.seek(0)

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
            'photo': (image_stream, 'photo.jpg'),
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
            self.assertGreater(len(upload_response.data), 0)
            self.assertTrue(upload_response.data.startswith(b'\xff\xd8'))

    def test_citizen_report_does_not_create_duplicate_incident_for_recent_same_barangay_report(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        response1 = self.client.post('/citizen-report', data={
            'hazard_type': 'flood',
            'severity': 'high',
            'location': 'Barangay Test',
            'description': 'Water rising',
            'affected_people': '5',
            'injuries': '0',
            'contact': '09170000000',
            'gps_lat': '14.1234',
            'gps_lng': '121.5678',
            'province_id': 1,
            'municipality_id': 1,
            'barangay_id': 1,
        }, follow_redirects=True)

        response2 = self.client.post('/citizen-report', data={
            'hazard_type': 'flood',
            'severity': 'high',
            'location': 'Barangay Test',
            'description': 'Water rising again',
            'affected_people': '6',
            'injuries': '0',
            'contact': '09170000000',
            'gps_lat': '14.1234',
            'gps_lng': '121.5678',
            'province_id': 1,
            'municipality_id': 1,
            'barangay_id': 1,
        }, follow_redirects=True)

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.count(), 2)
            self.assertEqual(Incident.query.filter_by(reported_by='citizen').count(), 1)

    def test_citizen_report_rejects_invalid_photo_upload(self):
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
            'photo': (BytesIO(b'not-an-image'), 'evil.exe'),
        }, content_type='multipart/form-data', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Photo upload was invalid.', response.data)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.count(), 0)
            self.assertEqual(Incident.query.count(), 0)

    def test_citizen_report_rejects_oversized_photo_upload(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        self.app.config['MAX_UPLOAD_SIZE_BYTES'] = 64

        image_stream = BytesIO()
        Image.new('RGB', (4, 4), color='white').save(image_stream, format='JPEG')
        image_stream.seek(0)

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
            'photo': (image_stream, 'photo.jpg', 'image/jpeg'),
        }, content_type='multipart/form-data', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Photo upload was invalid.', response.data)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.count(), 0)
            self.assertEqual(Incident.query.count(), 0)

    def test_citizen_report_rejects_photo_with_unsupported_mimetype(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        image_stream = BytesIO()
        Image.new('RGB', (1, 1), color='white').save(image_stream, format='JPEG')
        image_stream.seek(0)

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
            'photo': (image_stream, 'photo.jpg', 'application/octet-stream'),
        }, content_type='multipart/form-data', follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Photo upload was invalid.', response.data)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.count(), 0)
            self.assertEqual(Incident.query.count(), 0)

    def test_municipalities_api_scoped_to_requested_province(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        with self.app.app_context():
            rizal = Province.query.filter_by(name='Rizal').first()

        response = self.client.get(f'/api/municipalities/{rizal.id}')
        self.assertEqual(response.status_code, 200)
        names = {m['name'] for m in response.get_json()['municipalities']}
        self.assertIn('Angono', names)
        self.assertNotIn('Lipa City', names, 'a Batangas municipality must not appear under Rizal')

    def test_municipalities_api_requires_login(self):
        response = self.client.get('/api/municipalities/1')
        self.assertEqual(response.status_code, 401)

    def test_barangays_api_scoped_to_requested_municipality(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        with self.app.app_context():
            angono = Municipality.query.filter_by(name='Angono').first()

        response = self.client.get(f'/api/barangays/{angono.id}')
        self.assertEqual(response.status_code, 200)
        names = {b['name'] for b in response.get_json()['barangays']}
        self.assertEqual(len(names), 10)
        self.assertIn('Bagumbayan', names)

    def test_barangays_api_requires_login(self):
        response = self.client.get('/api/barangays/1')
        self.assertEqual(response.status_code, 401)

    def test_citizen_report_page_does_not_embed_full_municipality_and_barangay_lists(self):
        """Regression guard for the page-weight problem the AJAX cascade
        replaced: municipalities/barangays must be fetched on demand via
        /api/municipalities/<id> and /api/barangays/<id>, not dumped as
        thousands of <option> tags on every page load."""
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        response = self.client.get('/citizen-report')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertLess(html.count('<option'), 30)
        self.assertNotIn('Lipa City', html, 'municipality names should not be server-rendered into the page')

    def test_citizen_report_rejects_municipality_that_does_not_belong_to_submitted_province(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        with self.app.app_context():
            batangas = Province.query.filter_by(name='Batangas').first()
            angono = Municipality.query.filter_by(name='Angono').first()  # actually in Rizal

        response = self.client.post('/citizen-report', data={
            'hazard_type': 'flood',
            'severity': 'high',
            'location': 'Barangay Test',
            'description': 'Water rising',
            'province_id': batangas.id,
            'municipality_id': angono.id,
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'does not match the selected province', response.data)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.filter_by(municipality_id=angono.id).count(), 0)

    def test_citizen_report_rejects_barangay_that_does_not_belong_to_submitted_municipality(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'user'

        with self.app.app_context():
            lipa = Municipality.query.filter_by(name='Lipa City').first()
            angono = Municipality.query.filter_by(name='Angono').first()
            foreign_barangay = Barangay.query.filter_by(municipality_id=angono.id).first()  # in Rizal, not Lipa

        response = self.client.post('/citizen-report', data={
            'hazard_type': 'flood',
            'severity': 'high',
            'location': 'Barangay Test',
            'description': 'Water rising',
            'municipality_id': lipa.id,
            'barangay_id': foreign_barangay.id,
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'does not match the selected municipality', response.data)
        with self.app.app_context():
            self.assertEqual(CitizenReport.query.filter_by(barangay_id=foreign_barangay.id).count(), 0)

    def test_map_pins_endpoint_returns_active_incidents_with_coordinates(self):
        with self.app.app_context():
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
            db.session.flush()

            incident = Incident(
                user_id=1,
                hazard_type='flood',
                location='Barangay Test',
                message='Water rising',
                level='high',
                alert=True,
                status='ACTIVE',
                reported_by='citizen',
                citizen_report_id=citizen_report.id,
            )
            db.session.add(incident)
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

        with patch.object(scheduler, 'get_all_weather_data', return_value={'Lipa': weather_data}), \
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

        with patch.object(scheduler, 'get_all_weather_data', return_value={'Lipa': weather_data}), \
             patch.object(scheduler, 'predict_hazard', side_effect=fake_predict_hazard):
            with self.app.app_context():
                scheduler.monitor_hazards()

        with self.app.app_context():
            incidents = Incident.query.filter(Incident.hazard_type.in_(['flood', 'landslide'])).all()
            self.assertEqual(len(incidents), 2)
            self.assertEqual({incident.hazard_type for incident in incidents}, {'flood', 'landslide'})

    def test_api_realtime_data_returns_all_calabarzon_cities(self):
        weather_data = {
            'city': 'Cavite',
            'temperature': 30,
            'humidity': 70,
            'pressure': 1009,
            'wind_speed': 5,
            'rainfall': 2,
            'weather': 'sunny',
            'fetched_at': 'now',
        }

        with patch.object(app_module, 'get_all_weather_data', return_value={'Cavite': weather_data}), \
             patch.object(app_module, 'get_earthquake_data', return_value=[]):
            with self.client.session_transaction() as session:
                session['username'] = 'responder1'
                session['role'] = 'field_responder'

            response = self.client.get('/api/realtime-data')
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn('weather', data)
            self.assertIn('earthquakes', data)
            self.assertEqual(data['weather']['Cavite']['city'], 'Cavite')

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

    def test_commander_update_task_rejects_task_from_unowned_response(self):
        """A commander must not be able to mutate a task belonging to a
        different commander's response, even if they own *some* response
        and pass its id in the URL. Mirrors
        test_coordinator_update_task_rejects_unowned_task's coverage of the
        coordinator side of this same class of bug."""
        with self.app.app_context():
            commander_a = User(username='commanderA', email='ca@example.com', password='secret', role='incident_commander', agency='BFP', email_verified=True)
            commander_b = User(username='commanderB', email='cb@example.com', password='secret', role='incident_commander', agency='PNP', email_verified=True)
            db.session.add_all([commander_a, commander_b])
            db.session.commit()

            incident_a = Incident(hazard_type='flood', location='A', message='m', level='HIGH', status='ACTIVE')
            incident_b = Incident(hazard_type='flood', location='B', message='m', level='HIGH', status='ACTIVE')
            db.session.add_all([incident_a, incident_b])
            db.session.commit()

            response_a = IncidentResponse(incident_id=incident_a.id, commander_id=commander_a.id, status='ACTIVE')
            response_b = IncidentResponse(incident_id=incident_b.id, commander_id=commander_b.id, status='ACTIVE')
            db.session.add_all([response_a, response_b])
            db.session.commit()

            task_b = Task(
                incident_response_id=response_b.id,
                assigned_to_agency='PNP',
                assigned_by_id=commander_b.id,
                title='Belongs to commander B',
                description='Should not be mutable by commander A',
                status='PENDING',
            )
            db.session.add(task_b)
            db.session.commit()
            response_a_id, task_b_id = response_a.id, task_b.id

        with self.client.session_transaction() as session:
            session['username'] = 'commanderA'
            session['role'] = 'incident_commander'
            session['agency'] = 'BFP'

        # Commander A owns response_a_id, but task_b_id belongs to response_b.
        result = self.client.post(
            f'/incident-response/{response_a_id}/update-task/{task_b_id}',
            data={'status': 'COMPLETED'},
        )
        self.assertEqual(result.status_code, 404)
        with self.app.app_context():
            refreshed = db.session.get(Task, task_b_id)
            self.assertEqual(refreshed.status, 'PENDING')

    def test_commander_update_resource_rejects_resource_from_unowned_response(self):
        with self.app.app_context():
            commander_a = User(username='commanderC', email='cc@example.com', password='secret', role='incident_commander', agency='BFP', email_verified=True)
            commander_b = User(username='commanderD', email='cd@example.com', password='secret', role='incident_commander', agency='PNP', email_verified=True)
            db.session.add_all([commander_a, commander_b])
            db.session.commit()

            incident_a = Incident(hazard_type='flood', location='A', message='m', level='HIGH', status='ACTIVE')
            incident_b = Incident(hazard_type='flood', location='B', message='m', level='HIGH', status='ACTIVE')
            db.session.add_all([incident_a, incident_b])
            db.session.commit()

            response_a = IncidentResponse(incident_id=incident_a.id, commander_id=commander_a.id, status='ACTIVE')
            response_b = IncidentResponse(incident_id=incident_b.id, commander_id=commander_b.id, status='ACTIVE')
            db.session.add_all([response_a, response_b])
            db.session.commit()

            resource_b = Resource(
                incident_response_id=response_b.id,
                resource_type='Ambulance',
                agency='PNP',
                quantity=1,
                status='AVAILABLE',
            )
            db.session.add(resource_b)
            db.session.commit()
            response_a_id, resource_b_id = response_a.id, resource_b.id

        with self.client.session_transaction() as session:
            session['username'] = 'commanderC'
            session['role'] = 'incident_commander'
            session['agency'] = 'BFP'

        result = self.client.post(
            f'/incident-response/{response_a_id}/update-resource/{resource_b_id}',
            data={'status': 'DEPLOYED'},
        )
        self.assertEqual(result.status_code, 404)
        with self.app.app_context():
            refreshed = db.session.get(Resource, resource_b_id)
            self.assertEqual(refreshed.status, 'AVAILABLE')

    def test_register_rate_limited_after_five_requests_per_hour(self):
        statuses = []
        for i in range(7):
            response = self.client.post('/register', data={
                'username': f'ratelimit_user_{i}', 'email': f'ratelimit{i}@example.com',
                'password': 'SomePass123', 'full_name': 'Test User', 'contact_number': '09171234567',
            })
            statuses.append(response.status_code)
        self.assertIn(429, statuses)
        self.assertEqual(statuses.count(429), 2, "5-per-hour limit should allow exactly 5 through before limiting the remaining 2")

    def test_emergency_sos_rate_limited_after_five_requests_per_minute(self):
        """Regression test for the fix itself: emergency_sos() lives in
        blueprints/citizen.py, which is imported before `limiter` exists in
        app.py -- @limiter.limit can't be used as a decorator there without
        a circular import, so the limit is applied programmatically to the
        already-registered view function in app.view_functions instead.
        Worth a dedicated real-request test specifically because that
        pattern is easy to get subtly wrong (e.g. calling the decorator
        without reassigning its result back)."""
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'citizen'

        statuses = []
        for i in range(7):
            response = self.client.post('/emergency-sos', json={'location': f'Test {i}'})
            statuses.append(response.status_code)
        self.assertIn(429, statuses)
        self.assertEqual(statuses.count(200), 5, "5-per-minute limit should allow exactly 5 successful SOS submissions before limiting")

    def test_dashboard_stats_requires_login(self):
        """Migrated from the root-level tmp_debug_eoc_request.py-style
        manual scratch check into a real assertion. /api/dashboard-stats
        had no pytest coverage at all before this."""
        result = self.client.get('/api/dashboard-stats')
        self.assertEqual(result.status_code, 401)

    def test_dashboard_stats_returns_numeric_risk_score_and_magnitude(self):
        """Migrated from the root-level test_ai_prediction.py scratch
        script: that script's actual concern was that latest_risk_score /
        latest_earthquake_magnitude come back as numbers Jinja's
        "%.0f"|format(...) filter can consume without raising, not just
        that the endpoint returns 200. A None or string value here would
        pass a naive '200 OK' check but still break the citizen dashboard
        and ai_prediction.html template that render these values."""
        with self.app.app_context():
            citizen = User(username='dashboard_citizen', email='dc@example.com', password='secret', role='citizen', email_verified=True)
            db.session.add(citizen)
            db.session.commit()
            incident = Incident(
                user_id=citizen.id, hazard_type='flood', location='Test', message='m',
                status='ACTIVE', alert=True, score=62.5,
            )
            db.session.add(incident)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'dashboard_citizen'
            session['role'] = 'citizen'

        result = self.client.get('/api/dashboard-stats')
        self.assertEqual(result.status_code, 200)
        payload = result.get_json()
        for key in ('alert_count', 'total_incidents', 'latest_risk_score', 'latest_earthquake_magnitude'):
            self.assertIn(key, payload)
        # The actual bug class this guards against: these must format
        # cleanly as numbers, matching how the dashboard templates use them.
        formatted_score = "%.0f" % float(payload['latest_risk_score'])
        formatted_magnitude = "%.1f" % float(payload['latest_earthquake_magnitude'])
        self.assertTrue(formatted_score)
        self.assertTrue(formatted_magnitude)
        self.assertEqual(float(payload['latest_risk_score']), 62.5)

    def test_pending_sos_incidents_requires_eoc(self):
        response = self.client.get('/eoc/sos-incidents/pending')
        self.assertEqual(response.status_code, 401)

    def test_pending_sos_incidents_returns_unverified_emergency_incidents(self):
        with self.app.app_context():
            citizen = User(username='sos_test_citizen', email='stc@example.com', password='secret', role='citizen', email_verified=True)
            db.session.add(citizen)
            db.session.commit()
            sos_incident = Incident(
                user_id=citizen.id, hazard_type='EMERGENCY', location='Barangay Uno',
                message='EMERGENCY SOS Alert from citizen', level='CRITICAL',
                alert=True, status='NEW', reported_by='citizen',
            )
            # A non-SOS incident and an already-verified SOS incident must
            # NOT show up -- only unverified 'EMERGENCY' incidents count.
            other_incident = Incident(hazard_type='flood', location='Elsewhere', message='m', level='HIGH', status='NEW')
            verified_sos = Incident(
                user_id=citizen.id, hazard_type='EMERGENCY', location='Already handled',
                message='m', level='CRITICAL', status='VERIFIED', reported_by='citizen',
            )
            db.session.add_all([sos_incident, other_incident, verified_sos])
            db.session.commit()
            sos_incident_id = sos_incident.id

        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'eoc_staff'

        response = self.client.get('/eoc/sos-incidents/pending')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        ids = [i['id'] for i in payload['incidents']]
        self.assertEqual(ids, [sos_incident_id])
        self.assertEqual(payload['incidents'][0]['location'], 'Barangay Uno')

    def test_pending_sos_incidents_empties_once_verified(self):
        """This is the actual fix: an SOS incident stops alerting the moment
        any EOC staffer takes the real verify action, not a separate
        cosmetic 'dismiss' -- so a genuine emergency can't be silently
        marked seen without anyone actually acting on it."""
        with self.app.app_context():
            citizen = User(username='sos_test_citizen2', email='stc2@example.com', password='secret', role='citizen', email_verified=True)
            db.session.add(citizen)
            db.session.commit()
            sos_incident = Incident(
                user_id=citizen.id, hazard_type='EMERGENCY', location='Barangay Dos',
                message='EMERGENCY SOS Alert from citizen', level='CRITICAL',
                alert=True, status='NEW', reported_by='citizen',
            )
            db.session.add(sos_incident)
            db.session.commit()
            sos_incident_id = sos_incident.id

        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'eoc_staff'

        before = self.client.get('/eoc/sos-incidents/pending').get_json()
        self.assertEqual(len(before['incidents']), 1)

        self.client.post(f'/admin/incidents/{sos_incident_id}/verify', data={})

        after = self.client.get('/eoc/sos-incidents/pending').get_json()
        self.assertEqual(after['incidents'], [])

    def test_eoc_dashboard_includes_sos_alert_banner_and_polling(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'eoc_staff'

        response = self.client.get('/eoc-dashboard')
        html = response.get_data(as_text=True)
        self.assertIn('sosAlertBanner', html)
        self.assertIn('/eoc/sos-incidents/pending', html)

    def test_create_default_admin_does_not_touch_password_of_differently_named_admin(self):
        """Regression test for a real bug found while testing the
        forced-password-change flow: create_default_admin() used to fall
        back to `User.query.filter_by(role='admin').first()` when no user
        was named 'admin' or emailed 'admin@dics-ai.local'. That's
        dangerously broad -- it matches ANY admin-role account, including
        one who legitimately changed their password through this exact
        change-password flow. If ADMIN_PASSWORD in the ops .env file was
        never updated to match, the next server restart would silently
        revert that admin's password back to the stale env value and force
        them to change it again. create_default_admin() must now only ever
        touch an account it can identify by the stable 'admin' username or
        'admin@dics-ai.local' email -- never by role alone."""
        with self.app.app_context():
            real_admin = User(
                username='juan_delacruz', email='juan@lgu.example', password='TheirOwnChosenPassword1',
                role='admin', email_verified=True, must_change_password=False,
            )
            db.session.add(real_admin)
            db.session.commit()

            create_default_admin()

            refreshed = User.query.filter_by(username='juan_delacruz').first()
            self.assertEqual(refreshed.password, 'TheirOwnChosenPassword1', "an admin identified only by role must never have their password touched")
            self.assertFalse(refreshed.must_change_password)

            # A separate, standard bootstrap admin should have been created
            # instead of hijacking the existing one.
            bootstrap_admin = User.query.filter_by(username='admin').first()
            self.assertIsNotNone(bootstrap_admin)
            self.assertNotEqual(bootstrap_admin.id, refreshed.id)

    def test_new_user_with_must_change_password_is_redirected_on_login(self):
        with self.app.app_context():
            forced_user = User(
                username='forced_admin', email='fa@example.com', password='OldPass123',
                role='admin', email_verified=True, must_change_password=True,
            )
            db.session.add(forced_user)
            db.session.commit()

        response = self.client.post('/', data={'username': 'forced_admin', 'password': 'OldPass123'}, follow_redirects=True)
        self.assertEqual(response.request.path, '/change-password')

    def test_must_change_password_blocks_navigation_to_other_routes(self):
        """Defense-in-depth check: even with a direct request to an
        unrelated URL, a session with must_change_password set can't
        navigate around the change-password page (see app.py's
        enforce_password_change before_request hook)."""
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'admin'
            session['must_change_password'] = True

        response = self.client.get('/admin/users', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/change-password', response.headers.get('Location', ''))

    def test_change_password_success_clears_flag_and_allows_navigation(self):
        with self.app.app_context():
            forced_user = User(
                username='forced_admin2', email='fa2@example.com', password='OldPass123',
                role='admin', email_verified=True, must_change_password=True,
            )
            db.session.add(forced_user)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'forced_admin2'
            session['role'] = 'admin'
            session['must_change_password'] = True

        response = self.client.post('/change-password', data={
            'current_password': 'OldPass123',
            'new_password': 'BrandNewPass456',
            'confirm_password': 'BrandNewPass456',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            refreshed = User.query.filter_by(username='forced_admin2').first()
            self.assertFalse(refreshed.must_change_password)

        # the enforcement hook must no longer redirect this session away
        self.assertEqual(self.client.get('/admin/users').status_code, 200)

    def test_change_password_rejects_wrong_current_password(self):
        with self.app.app_context():
            forced_user = User(
                username='forced_admin3', email='fa3@example.com', password='OldPass123',
                role='admin', email_verified=True, must_change_password=True,
            )
            db.session.add(forced_user)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'forced_admin3'
            session['role'] = 'admin'
            session['must_change_password'] = True

        response = self.client.post('/change-password', data={
            'current_password': 'WrongPassword',
            'new_password': 'BrandNewPass456',
            'confirm_password': 'BrandNewPass456',
        })
        self.assertIn(b'Current password is incorrect', response.data)
        with self.app.app_context():
            refreshed = User.query.filter_by(username='forced_admin3').first()
            self.assertTrue(refreshed.must_change_password)

    def test_change_password_rejects_short_new_password(self):
        with self.app.app_context():
            forced_user = User(
                username='forced_admin4', email='fa4@example.com', password='OldPass123',
                role='admin', email_verified=True, must_change_password=True,
            )
            db.session.add(forced_user)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'forced_admin4'
            session['role'] = 'admin'
            session['must_change_password'] = True

        response = self.client.post('/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'short', 'confirm_password': 'short',
        })
        self.assertIn(b'at least 8 characters', response.data)

    def test_change_password_rejects_mismatched_confirmation(self):
        with self.app.app_context():
            forced_user = User(
                username='forced_admin5', email='fa5@example.com', password='OldPass123',
                role='admin', email_verified=True, must_change_password=True,
            )
            db.session.add(forced_user)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'forced_admin5'
            session['role'] = 'admin'
            session['must_change_password'] = True

        response = self.client.post('/change-password', data={
            'current_password': 'OldPass123', 'new_password': 'BrandNewPass456', 'confirm_password': 'DoesNotMatch789',
        })
        self.assertIn(b'do not match', response.data)

    def test_emergency_sos_meta_csrf_token_round_trip(self):
        """The SOS button (static/js/app.js triggerSOS()) sends the CSRF
        token from a <meta name="csrf-token"> tag as an X-CSRFToken header,
        since it's a JSON fetch() with no HTML form to carry a hidden field.
        Verify that round trip actually works end-to-end with CSRF protection
        turned on (the test harness disables it globally by default), and
        that a request with no token is still rejected.
        """
        self.app.config.update(WTF_CSRF_ENABLED=True)
        try:
            with self.client.session_transaction() as session:
                session['username'] = 'responder1'
                session['role'] = 'citizen'

            page = self.client.get('/citizen-dashboard')
            html = page.get_data(as_text=True)
            match = __import__('re').search(r'name="csrf-token" content="([^"]+)"', html)
            self.assertIsNotNone(match, 'base.html is missing the csrf-token meta tag')
            token = match.group(1)

            no_token_response = self.client.post('/emergency-sos', json={'location': 'Test'})
            self.assertEqual(no_token_response.status_code, 400)

            with_token_response = self.client.post(
                '/emergency-sos',
                json={'location': 'Test'},
                headers={'X-CSRFToken': token},
            )
            self.assertEqual(with_token_response.status_code, 200)
            self.assertTrue(with_token_response.get_json().get('success'))
        finally:
            self.app.config.update(WTF_CSRF_ENABLED=False)

    def test_analytics_accessible_to_coordinator_and_commander_not_just_admin_eoc(self):
        """/analytics previously only allowed admin/EOC, which contradicted
        services.permissions.can_view_analytics() (and docs/permissions-matrix.md),
        both of which also grant coordinator and commander access."""
        with self.app.app_context():
            coordinator = User(username='analytics_coord', email='ac@example.com', password='secret', role='agency_coordinator', agency='BFP', email_verified=True)
            db.session.add(coordinator)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'analytics_coord'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        response = self.client.get('/analytics')
        self.assertEqual(response.status_code, 200)

    def test_analytics_denied_to_citizen(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'citizen'

        response = self.client.get('/analytics', follow_redirects=False)
        self.assertEqual(response.status_code, 302)

    def test_analytics_api_denied_to_citizen(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'citizen'

        response = self.client.get('/api/analytics-data')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'error': 'Forbidden'})

    def test_analytics_api_denied_to_anonymous_user(self):
        response = self.client.get('/api/analytics-data')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {'error': 'Unauthorized'})

    def test_analytics_api_allowed_for_coordinator(self):
        with self.app.app_context():
            coordinator = User(
                username='analytics_api_coord', email='aac@example.com', password='secret',
                role='agency_coordinator', agency='BFP', email_verified=True,
            )
            db.session.add(coordinator)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'analytics_api_coord'
            session['role'] = 'agency_coordinator'

        response = self.client.get('/api/analytics-data')
        self.assertEqual(response.status_code, 200)
        self.assertIn('incident_counts', response.get_json())

    def test_admin_denied_access_to_coordinator_dashboard(self):
        """Admin was previously let into /coordinator/* via
        is_admin_or_coordinator(), which meant an admin visiting these
        pages saw everything empty (get_coordinator_agency() reads
        admin.agency, which is normally blank) -- a confusing dead end
        rather than a real feature. Admin's role is pure administration;
        agency operations belong to coordinators only."""
        with self.app.app_context():
            admin = User(username='admin_only', email='admin_only@example.com', password='secret', role='admin', email_verified=True)
            db.session.add(admin)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'admin_only'
            session['role'] = 'admin'

        for path in ('/coordinator', '/coordinator/tasks', '/coordinator/team',
                     '/coordinator/resources', '/coordinator/resource-requests',
                     '/coordinator/reports', '/coordinator/comms'):
            with self.subTest(path=path):
                result = self.client.get(path, follow_redirects=False)
                self.assertEqual(result.status_code, 302)
                self.assertNotIn('/coordinator', result.headers.get('Location', ''))

    def test_agency_coordinator_still_has_access_to_coordinator_dashboard(self):
        """Companion to the admin-denial test above: the fix must narrow
        access to coordinators only, not lock coordinators out too."""
        with self.app.app_context():
            coordinator = User(username='coord_only', email='coord_only@example.com', password='secret', role='agency_coordinator', agency='BFP', email_verified=True)
            db.session.add(coordinator)
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'coord_only'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        result = self.client.get('/coordinator')
        self.assertEqual(result.status_code, 200)

    def test_coordinator_dashboard_uses_unambiguous_operational_status_labels(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        result = self.client.get('/coordinator')
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'Open Tasks', result.data)
        self.assertIn(b'Pending or in progress', result.data)
        self.assertIn(b'Open Responses', result.data)
        self.assertNotIn(b'Pending Tasks', result.data)

    def test_coordinator_tasks_are_paginated(self):
        with self.app.app_context():
            coordinator = User(
                username='pagination_coord', email='pagination@example.com', password='secret',
                role='agency_coordinator', agency='BFP', email_verified=True,
            )
            db.session.add(coordinator)
            db.session.flush()
            incident = Incident(
                user_id=coordinator.id, hazard_type='flood', location='Test',
                message='Test incident', status='ACTIVE',
            )
            db.session.add(incident)
            db.session.flush()
            response = IncidentResponse(
                incident_id=incident.id, commander_id=coordinator.id, status='ACTIVE',
            )
            db.session.add(response)
            db.session.flush()
            for index in range(26):
                db.session.add(Task(
                    incident_response_id=response.id,
                    assigned_to_agency='BFP',
                    assigned_by_id=coordinator.id,
                    title=f'Pagination task {index}',
                    description='Test task',
                ))
            db.session.commit()

        with self.client.session_transaction() as session:
            session['username'] = 'pagination_coord'
            session['role'] = 'agency_coordinator'
            session['agency'] = 'BFP'

        first_page = self.client.get('/coordinator/tasks')
        second_page = self.client.get('/coordinator/tasks?page=2')
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertIn(b'Page 2 of 2', second_page.data)
        self.assertIn(b'Pagination task 0', second_page.data)

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

            task = Task(
                incident_response_id=incident_response_id,
                assigned_to_agency='DILG',
                assigned_by_id=coordinator_id,
                title='Agency-owned task',
                description='Supports coordinator report submission',
                status='PENDING',
                priority='MEDIUM',
            )
            db.session.add(task)
            db.session.commit()

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
