import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy import create_engine, inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

os.environ['DATABASE_URL'] = 'sqlite://'

import models
from app import (
    app,
    db,
    limiter,
    migrate_dispatcher_alerts_sqlite,
    migrate_incident_commander_tables,
)
from models import (
    Alert,
    AIRecommendation,
    AuditEvent,
    Barangay,
    EvacuationCenter,
    Facility,
    Incident,
    IncidentMessage,
    Municipality,
    Province,
    Report,
    Resource,
    Task,
    User,
)
from ai.decision_support import (
    SYSTEM_PROMPT,
    _build_user_prompt,
    _deterministic_low_risk_exit,
)
from blueprints.admin import _build_pg_dump_command


class DatabaseSchemaTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            WTF_CSRF_ENABLED=False,
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_core_and_expanded_models_are_registered(self):
        self.assertTrue(User.__tablename__)
        self.assertFalse(hasattr(models, 'Agency'))
        self.assertTrue(Incident.__tablename__)
        self.assertTrue(Task.__tablename__)
        self.assertTrue(Resource.__tablename__)
        self.assertTrue(Alert.__tablename__)
        self.assertTrue(Report.__tablename__)
        self.assertTrue(IncidentMessage.__tablename__)
        self.assertFalse(hasattr(models, 'Message'))
        self.assertTrue(Province.__tablename__)
        self.assertIsNotNone(limiter)
        self.assertEqual(limiter._storage_uri, 'memory://')

    def test_startup_patch_adds_dispatcher_alert_columns_to_legacy_sqlite_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / 'legacy.sqlite3'
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    'CREATE TABLE alert ('
                    'id INTEGER PRIMARY KEY, incident_id INTEGER, user_id INTEGER, '
                    'title VARCHAR(200) NOT NULL, message TEXT NOT NULL, '
                    'severity VARCHAR(20), status VARCHAR(20), '
                    'created_at DATETIME, updated_at DATETIME)'
                )
                connection.execute(
                    "INSERT INTO alert (id, title, message, status) "
                    "VALUES (1, 'Existing advisory', 'Existing message', 'ACTIVE')"
                )
                connection.commit()

            with patch('app._resolve_sqlite_db_path', return_value=str(database_path)):
                migrate_dispatcher_alerts_sqlite()
                migrate_dispatcher_alerts_sqlite()

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {
                    row[1] for row in connection.execute('PRAGMA table_info("alert")')
                }
                audience = connection.execute(
                    'SELECT audience FROM alert WHERE id=1'
                ).fetchone()[0]
                indexes = {
                    row[1] for row in connection.execute('PRAGMA index_list("alert")')
                }

            self.assertIn('audience', columns)
            self.assertIn('external_event_id', columns)
            self.assertEqual(audience, 'PUBLIC')
            self.assertIn('uq_alert_external_event_id', indexes)
        self.assertTrue(Municipality.__tablename__)
        self.assertTrue(Barangay.__tablename__)
        self.assertTrue(Facility.__tablename__)
        self.assertTrue(EvacuationCenter.__tablename__)
        self.assertFalse(hasattr(models, 'ResourceRequest'))
        self.assertTrue(AIRecommendation.__tablename__)
        self.assertNotIn('recommended_agencies', AIRecommendation.__table__.columns)
        self.assertTrue(AuditEvent.__tablename__)

    def test_phase_four_migration_retires_roles_and_drops_agency_schema(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / 'migrations'
            / 'versions'
            / 'f4c1a9e2b730_scope_to_san_pablo_cdrrmo.py'
        )
        spec = importlib.util.spec_from_file_location('phase_four_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE "user" (id INTEGER PRIMARY KEY, role VARCHAR(50), '
                'is_disabled BOOLEAN NOT NULL DEFAULT 0, agency VARCHAR(100))'
            )
            connection.exec_driver_sql('CREATE INDEX ix_user_agency ON "user" (agency)')
            connection.exec_driver_sql(
                'CREATE TABLE task (id INTEGER PRIMARY KEY, remarks TEXT, assigned_to_agency VARCHAR(100))'
            )
            connection.exec_driver_sql('CREATE INDEX ix_task_assigned_to_agency ON task (assigned_to_agency)')
            connection.exec_driver_sql('CREATE INDEX ix_task_remarks ON task (remarks)')
            connection.exec_driver_sql(
                'CREATE TABLE resource (id INTEGER PRIMARY KEY, agency VARCHAR(100), resource_request_id INTEGER)'
            )
            connection.exec_driver_sql('CREATE INDEX ix_resource_agency ON resource (agency)')
            connection.exec_driver_sql('CREATE INDEX ix_resource_request_id ON resource (resource_request_id)')
            connection.exec_driver_sql('CREATE TABLE resource_request (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE agency (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql(
                'CREATE TABLE ai_recommendation (id INTEGER PRIMARY KEY, recommended_agencies TEXT)'
            )
            connection.exec_driver_sql(
                'CREATE INDEX ix_ai_recommendation_recommended_agencies '
                'ON ai_recommendation (recommended_agencies)'
            )
            connection.execute(text(
                'INSERT INTO "user" (id, role) VALUES '
                "(1, 'agency_coordinator'), (2, 'incident_commander')"
            ))
            connection.execute(text(
                "INSERT INTO task (id, remarks, assigned_to_agency) "
                "VALUES (1, 'Existing note', 'Legacy Agency')"
            ))

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            migrated_users = connection.execute(
                text('SELECT role, is_disabled FROM "user" ORDER BY id')
            ).fetchall()
            task = connection.execute(text('SELECT remarks FROM task WHERE id=1')).scalar_one()
            tables = set(inspect(connection).get_table_names())
            columns = {
                table: {column['name'] for column in inspect(connection).get_columns(table)}
                for table in ('user', 'task', 'resource', 'ai_recommendation')
            }
            indexes = {
                table: {index['name'] for index in inspect(connection).get_indexes(table)}
                for table in ('user', 'task', 'resource', 'ai_recommendation')
            }
            foreign_key_violations = connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()

        self.assertEqual(migrated_users, [('retired', 1), ('imt_staff', 0)])
        self.assertEqual(task, 'Existing note\nHistorical agency assignment: Legacy Agency')
        self.assertNotIn('agency', columns['user'])
        self.assertNotIn('assigned_to_agency', columns['task'])
        self.assertNotIn('agency', columns['resource'])
        self.assertNotIn('resource_request_id', columns['resource'])
        self.assertNotIn('recommended_agencies', columns['ai_recommendation'])
        self.assertNotIn('ix_user_agency', indexes['user'])
        self.assertNotIn('ix_task_assigned_to_agency', indexes['task'])
        self.assertIn('ix_task_remarks', indexes['task'])
        self.assertNotIn('ix_resource_agency', indexes['resource'])
        self.assertNotIn('ix_resource_request_id', indexes['resource'])
        self.assertNotIn('ix_ai_recommendation_recommended_agencies', indexes['ai_recommendation'])
        self.assertNotIn('agency', tables)
        self.assertNotIn('resource_request', tables)
        self.assertEqual(foreign_key_violations, [])
        engine.dispose()

    def test_user_email_has_case_insensitive_unique_index(self):
        db.session.add(User(username='email_case_one', email='Case@Example.com', password='secret'))
        db.session.commit()

        with self.assertRaises(IntegrityError):
            db.session.execute(User.__table__.insert().values(
                username='email_case_two',
                email='CASE@example.com',
                password='secret',
            ))
            db.session.commit()
        db.session.rollback()

    def test_legacy_password_migration_hashes_plaintext_and_preserves_hashes(self):
        migration_path = Path(__file__).resolve().parents[1] / 'migrations' / 'versions' / 'b3c8d4e2f190_hash_legacy_plaintext_passwords.py'
        spec = importlib.util.spec_from_file_location('legacy_password_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        existing_hash = generate_password_hash('already-hashed-password')
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY, password TEXT NOT NULL)')
            connection.execute(text('INSERT INTO "user" (id, password) VALUES (1, :password), (2, :password_hash)'), {
                'password': 'legacy-test-password',
                'password_hash': existing_hash,
            })
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            rows = dict(connection.execute(text('SELECT id, password FROM "user"')).all())
            self.assertTrue(check_password_hash(rows[1], 'legacy-test-password'))
            self.assertNotEqual(rows[1], 'legacy-test-password')
            self.assertEqual(rows[2], existing_hash)
        engine.dispose()

    def test_task_remarks_migration_adds_column_and_converts_failed_tasks(self):
        migration_path = Path(__file__).resolve().parents[1] / 'migrations' / 'versions' / 'c4a72f8d1e63_add_task_remarks_and_migrate_failed_status.py'
        spec = importlib.util.spec_from_file_location('task_remarks_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE task (id INTEGER PRIMARY KEY, status VARCHAR(20))'
            )
            connection.execute(text(
                "INSERT INTO task (id, status) VALUES (1, 'FAILED'), (2, 'failed')"
            ))

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            columns = {column['name'] for column in inspect(connection).get_columns('task')}
            rows = connection.execute(text('SELECT id, status, remarks FROM task ORDER BY id')).all()
            self.assertIn('remarks', columns)
            self.assertEqual(rows[0], (1, 'COMPLETED', migration.MIGRATED_FAILED_REMARK))
            self.assertEqual(rows[1], (2, 'COMPLETED', migration.MIGRATED_FAILED_REMARK))

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            self.assertEqual(connection.execute(text('SELECT COUNT(*) FROM task')).scalar_one(), 2)
        engine.dispose()

    def test_sqlite_startup_patch_adds_task_remarks_to_legacy_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / 'legacy.sqlite3'
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
                connection.execute('CREATE TABLE incident (id INTEGER PRIMARY KEY)')
                connection.execute(
                    'CREATE TABLE incident_response ('
                    'id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL UNIQUE, '
                    'commander_id INTEGER NOT NULL, status VARCHAR(20), '
                    'situation_summary TEXT, priority_level VARCHAR(20), '
                    'affected_population INTEGER, started_at DATETIME, resolved_at DATETIME, '
                    'closed_at DATETIME, updated_at DATETIME)'
                )
                connection.execute(
                    'CREATE TABLE task ('
                    'id INTEGER PRIMARY KEY, incident_response_id INTEGER NOT NULL, '
                    'assigned_to_agency VARCHAR(150) NOT NULL, assigned_by_id INTEGER NOT NULL, '
                    'title VARCHAR(200) NOT NULL, description TEXT NOT NULL, status VARCHAR(20), '
                    'remarks TEXT, priority VARCHAR(20), estimated_completion DATETIME, '
                    'completed_at DATETIME, created_at DATETIME, updated_at DATETIME)'
                )
                connection.execute('INSERT INTO "user" (id) VALUES (1)')
                connection.execute('INSERT INTO incident (id) VALUES (1)')
                connection.execute(
                    "INSERT INTO incident_response (id, incident_id, commander_id, status) "
                    "VALUES (1, 1, 1, 'ACTIVE')"
                )
                connection.execute(
                    "INSERT INTO task (id, incident_response_id, assigned_to_agency, "
                    "assigned_by_id, title, description, status) "
                    "VALUES (1, 1, 'Legacy Agency', 1, 'Legacy task', 'Keep this task', 'PENDING')"
                )
                connection.commit()

            with patch('app._resolve_sqlite_db_path', return_value=str(database_path)):
                migrate_incident_commander_tables()
                migrate_incident_commander_tables()

            with closing(sqlite3.connect(database_path)) as connection:
                columns = {row[1] for row in connection.execute('PRAGMA table_info(task)')}
                task_column_info = {
                    row[1]: row for row in connection.execute('PRAGMA table_info(task)')
                }
                response_column_info = {
                    row[1]: row for row in connection.execute('PRAGMA table_info(incident_response)')
                }
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                legacy_task = connection.execute(
                    'SELECT remarks, title, description FROM task WHERE id=1'
                ).fetchone()
                legacy_response_commander = connection.execute(
                    'SELECT commander_id FROM incident_response WHERE id=1'
                ).fetchone()[0]
                task_count = connection.execute('SELECT COUNT(*) FROM task').fetchone()[0]
            self.assertIn('remarks', columns)
            self.assertIn('assigned_to_user_id', columns)
            self.assertIn('assigned_to_team_id', columns)
            self.assertNotIn('assigned_to_agency', columns)
            self.assertEqual(response_column_info['commander_id'][3], 0)
            self.assertIn('dispatched_by_id', response_column_info)
            self.assertIn('response_team_members', tables)
            self.assertIn('barangay_response_unit', tables)
            self.assertIn('barangay_contact_log', tables)
            self.assertEqual(
                legacy_task,
                ('Historical agency assignment: Legacy Agency', 'Legacy task', 'Keep this task'),
            )
            self.assertEqual(legacy_response_commander, 1)
            self.assertEqual(task_count, 1)

    def test_ai_prediction_route_redirects_to_dispatch_decision_support(self):
        test_user = User(username='test_imt', email='test@example.com', password='pass', role='imt_staff')
        db.session.add(test_user)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess['username'] = 'test_imt'
            sess['role'] = 'imt_staff'

        response = self.client.post('/ai-prediction', data={
            'hazard_type': 'flood',
            'rainfall': '120',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/dispatcher/decision-support')
        self.assertEqual(Incident.query.filter_by(reported_by='ai_prediction').count(), 0)
        self.assertEqual(AIRecommendation.query.count(), 0)

    def test_incident_tracks_external_event_id_for_scheduler_deduplication(self):
        self.assertIn('external_event_id', Incident.__table__.columns.keys())
        self.assertIsNotNone(Incident.__table__.c.external_event_id)

    def test_citizen_report_migration_includes_gps_accuracy_column(self):
        migration_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'migrations',
            'versions',
            'd3f7b1a9c4e2_add_citizen_report_gps_accuracy.py',
        )
        self.assertTrue(os.path.exists(migration_path))
        with open(migration_path, 'r', encoding='utf-8') as migration_file:
            migration_text = migration_file.read()
        self.assertIn('gps_accuracy', migration_text)
        self.assertIn("op.add_column('citizen_report'", migration_text)

    @patch('blueprints.admin.shutil.which', return_value='pg_dump')
    def test_pg_dump_command_uses_custom_format_without_password_in_argv(self, mock_which):
        command, environment = _build_pg_dump_command(
            'postgresql://backup_user:secret@ep.example.neon.tech:5433/dics?sslmode=require',
            '/tmp/dics_ai_backup.dump',
        )

        mock_which.assert_called_once_with('pg_dump')
        self.assertEqual(command[0], 'pg_dump')
        self.assertIn('--format=custom', command)
        self.assertIn('--no-owner', command)
        self.assertIn('--no-privileges', command)
        self.assertIn('--file=/tmp/dics_ai_backup.dump', command)
        self.assertIn('--dbname', command)
        self.assertNotIn('secret', ' '.join(command))
        self.assertEqual(environment['PGPASSWORD'], 'secret')
        self.assertIn('sslmode=require', command[-1])

    @patch('blueprints.admin.shutil.which', return_value=None)
    def test_pg_dump_command_fails_when_client_is_missing(self, mock_which):
        with self.assertRaisesRegex(RuntimeError, 'pg_dump is not installed'):
            _build_pg_dump_command(
                'postgresql://backup_user:secret@localhost/dics',
                '/tmp/dics_ai_backup.dump',
            )

        mock_which.assert_called_once_with('pg_dump')

    def test_deterministic_low_risk_exit_bypasses_ai_when_inputs_are_conservatively_low(self):
        result = _deterministic_low_risk_exit(
            'flood',
            rainfall_mm=5,
            river_level_m=0.5,
            humidity_pct=70,
            population_density=200,
            earthquake_data=[{'magnitude': 3.1, 'place': 'nearby'}],
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['level'], 'Low')
        self.assertEqual(result['confidence'], 95.0)
        self.assertNotIn('recommended_agencies', result)
        self.assertIn('rainfall below 10 mm', result['primary_factors'])

    def test_deterministic_low_risk_exit_does_not_treat_missing_inputs_as_low_risk(self):
        for missing_field in ('river_level_m', 'humidity_pct', 'population_density'):
            inputs = {
                'rainfall_mm': 5,
                'river_level_m': 0.5,
                'humidity_pct': 70,
                'population_density': 200,
                'earthquake_data': [{'magnitude': 3.1, 'place': 'nearby'}],
            }
            inputs[missing_field] = None

            with self.subTest(missing_field=missing_field):
                self.assertIsNone(_deterministic_low_risk_exit('flood', **inputs))

    def test_ai_prompt_identifies_unavailable_river_gauge_data(self):
        prompt = _build_user_prompt('flood', 20, None, 85, 1200)

        self.assertIn(
            'River level: unavailable (routine gap: no river gauge data; continue assessment using available readings)',
            prompt,
        )
        self.assertNotIn('River level: None m', prompt)

    def test_ai_system_prompt_uses_san_pablo_operating_scope(self):
        self.assertIn('San Pablo City, Laguna', SYSTEM_PROMPT)
        self.assertNotIn('CALABARZON', SYSTEM_PROMPT)


if __name__ == '__main__':
    unittest.main()
