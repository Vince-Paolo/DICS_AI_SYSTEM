import importlib.util
import os
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_emergency_log.db'))
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app import app, db
from models import AuditEvent, Barangay, Incident, Municipality, PatientRecord, Province, User
import app as app_module


class EmergencyLogTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            province = Province(code='0434', name='Laguna')
            db.session.add(province)
            db.session.flush()
            self.province_id = province.id
            db.session.add_all([
                User(username='log_imt', email='log-imt@example.com', password='secret',
                     role='imt_staff', email_verified=True),
                User(username='log_dispatcher', email='log-dispatcher@example.com', password='secret',
                     role='dispatcher', email_verified=True),
                User(username='log_citizen', email='log-citizen@example.com', password='secret',
                     role='citizen', email_verified=True),
                User(username='log_responder', email='log-responder@example.com', password='secret',
                     role='field_responder', email_verified=True),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def _login(self, username, role):
        with self.client.session_transaction() as session:
            session['username'] = username
            session['role'] = role

    @staticmethod
    def _incident(hazard_type='EMERGENCY', location='Main Street', created_at=None):
        return Incident(
            hazard_type=hazard_type,
            location=location,
            message='Test incident',
            status='NEW',
            created_at=created_at,
        )

    def test_control_numbers_increment_and_roll_over_by_creation_year(self):
        with self.app.app_context():
            incidents = [
                self._incident(created_at=datetime(2025, 12, 31, 15, 59)),
                self._incident(created_at=datetime(2025, 1, 1)),
                self._incident(created_at=datetime(2026, 1, 1)),
            ]
            db.session.add_all(incidents)
            db.session.commit()

            self.assertEqual(
                [incident.control_number for incident in incidents],
                ['2025-00001', '2025-00002', '2026-00001'],
            )

    def test_control_number_year_rolls_over_at_midnight_in_manila(self):
        with self.app.app_context():
            incidents = [
                self._incident(created_at=datetime(2026, 12, 31, 15, 59)),
                self._incident(created_at=datetime(2026, 12, 31, 16, 0)),
                self._incident(created_at=datetime(2026, 12, 31, 16, 1, tzinfo=timezone.utc)),
            ]
            db.session.add_all(incidents)
            db.session.commit()

            self.assertEqual(
                [incident.control_number for incident in incidents],
                ['2026-00001', '2027-00001', '2027-00002'],
            )

    def test_parallel_incident_inserts_get_unique_control_numbers(self):
        def create_incident(index):
            with self.app.app_context():
                incident = self._incident(location=f'Concurrent {index}')
                db.session.add(incident)
                db.session.commit()
                number = incident.control_number
                db.session.remove()
                return number

        with ThreadPoolExecutor(max_workers=8) as executor:
            numbers = list(executor.map(create_incident, range(24)))

        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(
            sorted(int(number.split('-')[1]) for number in numbers),
            list(range(1, 25)),
        )

    def test_emergency_log_searches_and_sorts_by_control_number(self):
        with self.app.app_context():
            older = self._incident(location='Older location', created_at=datetime(2024, 1, 1))
            newer = self._incident(location='Newest location', created_at=datetime(2026, 1, 1))
            db.session.add_all([
                older,
                newer,
                self._incident(hazard_type='Flood', location='Hazard feed', created_at=datetime(2027, 1, 1)),
            ])
            db.session.commit()
            expected_detail_ids = [str(newer.id), str(older.id)]

        self._login('log_imt', 'imt_staff')
        response = self.client.get('/eoc/emergency-log')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        detail_ids = re.findall(r'href="/eoc/emergency-log/(\d+)"', html)
        self.assertEqual(detail_ids, expected_detail_ids)
        self.assertNotIn('Hazard feed', html)

        response = self.client.get('/eoc/emergency-log?q=Older')
        self.assertIn('Older location', response.get_data(as_text=True))
        self.assertNotIn('Newest location', response.get_data(as_text=True))

    def test_global_control_number_search_opens_exact_emergency_only(self):
        with self.app.app_context():
            emergency = self._incident(created_at=datetime(2026, 1, 1))
            non_emergency = self._incident(
                hazard_type='Flood',
                location='Feed event',
                created_at=datetime(2026, 1, 2),
            )
            db.session.add_all([emergency, non_emergency])
            db.session.commit()
            emergency_number = emergency.control_number
            emergency_id = emergency.id
            hazard_number = non_emergency.control_number

        self._login('log_dispatcher', 'dispatcher')
        page = self.client.get('/eoc/emergency-log')
        self.assertIn('global-control-number', page.get_data(as_text=True))

        found = self.client.get(f'/eoc/emergency-search?q={emergency_number}')
        self.assertEqual(found.status_code, 302)
        self.assertEqual(found.headers['Location'], f'/eoc/emergency-log/{emergency_id}')

        not_emergency = self.client.get(f'/eoc/emergency-search?q={hazard_number}')
        self.assertEqual(not_emergency.status_code, 302)
        self.assertEqual(not_emergency.headers['Location'], '/eoc/emergency-log')
        not_emergency_page = self.client.get(not_emergency.headers['Location'])
        self.assertIn(
            'belongs to a Flood record, not a dispatcher-logged emergency',
            not_emergency_page.get_data(as_text=True),
        )

        invalid = self.client.get('/eoc/emergency-search?q=not-a-number')
        self.assertEqual(invalid.status_code, 302)
        self.assertEqual(invalid.headers['Location'], '/eoc/emergency-log')

        unknown = self.client.get('/eoc/emergency-search?q=9999-99999')
        self.assertEqual(unknown.status_code, 302)
        unknown_page = self.client.get(unknown.headers['Location'])
        self.assertIn('No incident was found with control number 9999-99999', unknown_page.get_data(as_text=True))

    def test_emergency_detail_has_tabs_summary_timeline_and_responder_nav(self):
        with self.app.app_context():
            incident = self._incident(created_at=datetime(2026, 1, 1))
            db.session.add(incident)
            db.session.commit()
            incident_id = incident.id

        self._login('log_imt', 'imt_staff')
        detail = self.client.get(f'/eoc/emergency-log/{incident_id}')
        html = detail.get_data(as_text=True)
        self.assertEqual(detail.status_code, 200)
        for tab_id in ('dispatch-panel', 'barangay-panel', 'decision-panel', 'patients-panel', 'timeline-panel'):
            self.assertIn(f'id="{tab_id}"', html)
        self.assertIn('aria-label="Emergency summary"', html)
        self.assertIn('Emergency timeline', html)

        self._login('log_responder', 'field_responder')
        responder_page = self.client.get('/responder-dashboard')
        responder_html = responder_page.get_data(as_text=True)
        self.assertEqual(responder_page.status_code, 200)
        self.assertIn('class="responder-bottom-nav"', responder_html)
        self.assertIn('href="/responder-tasks"', responder_html)
        self.assertIn('href="/responder-report"', responder_html)
        self.assertIn('href="/hazard-map"', responder_html)

    def test_dispatcher_logs_emergency_with_severity_and_san_pablo_barangay(self):
        with self.app.app_context():
            municipality = Municipality(code='043424', name='San Pablo City', province_id=self.province_id)
            db.session.add(municipality)
            db.session.flush()
            barangay = Barangay(municipality_id=municipality.id, code='043424001', name='San Roque')
            db.session.add(barangay)
            db.session.commit()
            barangay_id = barangay.id
            municipality_id = municipality.id

        self._login('log_dispatcher', 'dispatcher')
        log_page = self.client.get('/eoc/emergency-log')
        html = log_page.get_data(as_text=True)
        self.assertEqual(log_page.status_code, 200)
        self.assertIn('name="severity"', html)
        self.assertIn('name="barangay_id"', html)
        self.assertIn('San Roque', html)

        response = self.client.post('/dispatcher/emergency-log/new', data={
            'location': 'Near the market',
            'description': 'Road collision',
            'patient_count': '2',
            'severity': 'High',
            'barangay_id': str(barangay_id),
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            incident = Incident.query.filter_by(hazard_type='EMERGENCY').one()
            self.assertEqual(incident.level, 'High')
            self.assertEqual(incident.barangay_id, barangay_id)
            self.assertEqual(incident.municipality_id, municipality_id)

    def test_dispatcher_rejects_invalid_severity_or_non_san_pablo_barangay(self):
        with self.app.app_context():
            municipality = Municipality(code='043424', name='San Pablo City', province_id=self.province_id)
            other_municipality = Municipality(code='OTHER', name='Other City', province_id=self.province_id)
            db.session.add_all([municipality, other_municipality])
            db.session.flush()
            barangays = [
                Barangay(municipality_id=municipality.id, code='043424001', name='San Roque'),
                Barangay(municipality_id=other_municipality.id, code='OTHER001', name='Other Barangay'),
            ]
            db.session.add_all(barangays)
            db.session.commit()
            san_pablo_barangay_id, other_barangay_id = (barangay.id for barangay in barangays)

        self._login('log_dispatcher', 'dispatcher')
        for severity, barangay_id in (
            ('Urgent', san_pablo_barangay_id),
            ('High', other_barangay_id),
        ):
            response = self.client.post('/dispatcher/emergency-log/new', data={
                'location': 'Near the market',
                'description': 'Road collision',
                'patient_count': '2',
                'severity': severity,
                'barangay_id': str(barangay_id),
            })
            self.assertEqual(response.status_code, 302)

        with self.app.app_context():
            self.assertEqual(Incident.query.filter_by(hazard_type='EMERGENCY').count(), 0)

    def test_dispatcher_can_update_existing_emergency_severity_and_barangay(self):
        with self.app.app_context():
            municipality = Municipality(
                code='043424',
                name='San Pablo City',
                province_id=self.province_id,
            )
            db.session.add(municipality)
            db.session.flush()
            barangay = Barangay(
                municipality_id=municipality.id,
                code='043424001',
                name='San Roque',
            )
            incident = self._incident()
            db.session.add_all([barangay, incident])
            db.session.commit()
            incident_id = incident.id
            barangay_id = barangay.id
            municipality_id = municipality.id

        self._login('log_dispatcher', 'dispatcher')
        detail = self.client.get(f'/eoc/emergency-log/{incident_id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('incident-assessment-fields-title', detail.get_data(as_text=True))

        response = self.client.post(
            f'/eoc/emergency-log/{incident_id}/assessment',
            data={'severity': 'Severe', 'barangay_id': str(barangay_id)},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            incident = db.session.get(Incident, incident_id)
            self.assertEqual(incident.level, 'Severe')
            self.assertEqual(incident.barangay_id, barangay_id)
            self.assertEqual(incident.municipality_id, municipality_id)
            audit_event = AuditEvent.query.filter_by(
                entity_type='Incident',
                entity_id=incident_id,
                action='ASSESSMENT_FIELDS_UPDATED',
            ).one()
            self.assertIn('Severity changed from not recorded to Severe.', audit_event.details)
            self.assertIn('Barangay changed from not recorded to San Roque.', audit_event.details)

    def test_imt_can_update_existing_emergency_and_invalid_barangay_is_rejected(self):
        with self.app.app_context():
            municipality = Municipality(
                code='043424',
                name='San Pablo City',
                province_id=self.province_id,
            )
            db.session.add(municipality)
            db.session.flush()
            barangays = [
                Barangay(municipality_id=municipality.id, code='043424001', name='San Roque'),
                Barangay(municipality_id=municipality.id, code='043424002', name='San Rafael'),
            ]
            incident = self._incident()
            db.session.add_all([*barangays, incident])
            db.session.commit()
            incident_id = incident.id
            valid_barangay_id, second_barangay_id = (barangay.id for barangay in barangays)

        self._login('log_imt', 'imt_staff')
        invalid = self.client.post(
            f'/eoc/emergency-log/{incident_id}/assessment',
            data={'severity': 'Urgent', 'barangay_id': str(valid_barangay_id)},
        )
        self.assertEqual(invalid.status_code, 302)
        with self.app.app_context():
            incident = db.session.get(Incident, incident_id)
            self.assertIsNone(incident.level)
            self.assertIsNone(incident.barangay_id)

        valid = self.client.post(
            f'/eoc/emergency-log/{incident_id}/assessment',
            data={'severity': 'Moderate', 'barangay_id': str(second_barangay_id)},
        )
        self.assertEqual(valid.status_code, 302)
        with self.app.app_context():
            incident = db.session.get(Incident, incident_id)
            self.assertEqual(incident.level, 'Moderate')
            self.assertEqual(incident.barangay_id, second_barangay_id)

    def test_patient_records_are_imt_only_and_update_patient_count(self):
        with self.app.app_context():
            incident = self._incident()
            db.session.add(incident)
            db.session.commit()
            incident_id = incident.id

        for username, role in (
            ('log_citizen', 'citizen'),
            ('log_responder', 'field_responder'),
        ):
            self._login(username, role)
            self.assertEqual(self.client.get('/eoc/emergency-log').status_code, 403)
            self.assertEqual(
                self.client.get(f'/eoc/emergency-log/{incident_id}').status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    f'/eoc/emergency-log/{incident_id}/patients',
                    data={'condition': 'Minor injury'},
                ).status_code,
                403,
            )

        self._login('log_imt', 'imt_staff')
        response = self.client.post(
            f'/eoc/emergency-log/{incident_id}/patients',
            data={
                'condition': 'Minor injury',
                'destination': 'City Health Office',
                'status': 'RECEIVING_CARE',
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            incident = db.session.get(Incident, incident_id)
            self.assertEqual(incident.patient_count, 1)
            self.assertEqual(len(incident.patient_records), 1)
            self.assertEqual(incident.patient_records[0].condition, 'Minor injury')
            patient_id = incident.patient_records[0].id

        detail = self.client.get(f'/eoc/emergency-log/{incident_id}').get_data(as_text=True)
        self.assertIn('Minor injury', detail)
        self.assertIn('City Health Office', detail)
        update_response = self.client.post(
            f'/eoc/emergency-log/{incident_id}/patients/{patient_id}',
            data={
                'condition': 'Transferred for observation',
                'destination': 'District Hospital',
                'status': 'TRANSFERRED',
            },
        )
        self.assertEqual(update_response.status_code, 302)
        detail = self.client.get(f'/eoc/emergency-log/{incident_id}').get_data(as_text=True)
        self.assertIn('Transferred for observation', detail)
        self.assertIn('District Hospital', detail)
        self.assertIn('TRANSFERRED', detail)

        self.assertEqual(
            self.client.post(
                f'/eoc/emergency-log/{incident_id}/patient-count',
                data={'patient_count': '0'},
            ).status_code,
            302,
        )
        with self.app.app_context():
            self.assertEqual(db.session.get(Incident, incident_id).patient_count, 1)
            self.assertIsNotNone(db.session.get(PatientRecord, patient_id))

        delete_response = self.client.post(
            f'/eoc/emergency-log/{incident_id}/patients/{patient_id}/delete'
        )
        self.assertEqual(delete_response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(db.session.get(PatientRecord, patient_id))
            self.assertEqual(db.session.get(Incident, incident_id).patient_count, 1)

    def test_emergency_endpoints_hide_non_emergency_incidents(self):
        with self.app.app_context():
            incident = self._incident(hazard_type='Flood')
            db.session.add(incident)
            db.session.commit()
            incident_id = incident.id

        self._login('log_imt', 'imt_staff')
        self.assertEqual(self.client.get(f'/eoc/emergency-log/{incident_id}').status_code, 404)


class EmergencyControlNumberMigrationTestCase(unittest.TestCase):
    def test_sqlite_startup_patch_backfills_legacy_incidents_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = os.path.join(temp_dir, 'legacy.sqlite3')
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute("""
                    CREATE TABLE incident (
                        id INTEGER PRIMARY KEY,
                        created_at DATETIME,
                        event_time DATETIME
                    )
                """)
                connection.execute(
                    "INSERT INTO incident (id, created_at) VALUES (1, '2023-06-10 12:00:00')"
                )
                connection.execute(
                    "INSERT INTO incident (id, created_at) VALUES (2, '2026-12-31 16:00:00')"
                )
                connection.commit()

            with patch.object(
                app_module,
                '_resolve_sqlite_db_path',
                return_value=database_path,
            ):
                app_module.migrate_emergency_log_sqlite()
                app_module.migrate_emergency_log_sqlite()

            with closing(sqlite3.connect(database_path)) as connection:
                number, patient_count = connection.execute(
                    'SELECT control_number, patient_count FROM incident WHERE id = 1'
                ).fetchone()
                self.assertEqual((number, patient_count), ('2023-00001', 0))
                self.assertEqual(
                    connection.execute(
                        'SELECT control_number FROM incident WHERE id = 2'
                    ).fetchone()[0],
                    '2027-00001',
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT last_number FROM control_number_sequence WHERE year = 2023'
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        'SELECT last_number FROM control_number_sequence WHERE year = 2027'
                    ).fetchone()[0],
                    1,
                )

    def test_migration_backfills_historical_numbers_and_patient_schema(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / 'migrations'
            / 'versions'
            / 'd1e6a94b7c32_add_emergency_control_numbers_and_patients.py'
        )
        spec = importlib.util.spec_from_file_location('emergency_log_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE incident (
                    id INTEGER PRIMARY KEY,
                    hazard_type VARCHAR(50) NOT NULL,
                    location VARCHAR(255),
                    created_at DATETIME,
                    event_time DATETIME
                )
            """)
            connection.execute(text("""
                INSERT INTO incident (id, hazard_type, created_at) VALUES
                    (1, 'EMERGENCY', '2024-04-01 10:00:00'),
                    (2, 'EMERGENCY', '2024-04-02 10:00:00'),
                    (3, 'Flood', '2025-01-01 10:00:00'),
                    (4, 'EMERGENCY', '2026-12-31 15:59:00'),
                    (5, 'EMERGENCY', '2026-12-31 16:00:00')
            """))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            numbers = connection.execute(text(
                'SELECT id, control_number, patient_count FROM incident ORDER BY id'
            )).all()
            self.assertEqual(
                [(row[0], row[1], row[2]) for row in numbers],
                [
                    (1, '2024-00001', 0),
                    (2, '2024-00002', 0),
                    (3, '2025-00001', 0),
                    (4, '2026-00001', 0),
                    (5, '2027-00001', 0),
                ],
            )
            self.assertIn('condition', {
                column['name'] for column in inspect(connection).get_columns('patient_record')
            })
            sequence = dict(connection.execute(text(
                'SELECT year, last_number FROM control_number_sequence'
            )).all())
            self.assertEqual(sequence, {2024: 2, 2025: 1, 2026: 1, 2027: 1})
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
