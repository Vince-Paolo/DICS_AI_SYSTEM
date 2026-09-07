import json
import os
import unittest
from unittest.mock import patch

import models
from app import app, db, limiter
from models import (
    Agency,
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
    ResourceRequest,
    Task,
    User,
)
from ai.decision_support import (
    _build_user_prompt,
    _normalize_recommended_agencies,
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
        self.assertTrue(Agency.__tablename__)
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
        self.assertTrue(Municipality.__tablename__)
        self.assertTrue(Barangay.__tablename__)
        self.assertTrue(Facility.__tablename__)
        self.assertTrue(EvacuationCenter.__tablename__)
        self.assertTrue(ResourceRequest.__tablename__)
        self.assertTrue(AIRecommendation.__tablename__)
        self.assertTrue(AuditEvent.__tablename__)

    @patch('services.realtime_data.get_earthquake_data', return_value=[])
    @patch('blueprints.ai.predict_hazard')
    def test_ai_prediction_creates_recommendation_and_audit(self, mock_predict, mock_earthquake_data):
        mock_predict.return_value = {
            'provider': 'openai',
            'model': 'gpt-5.6-terra',
            'score': 78.2,
            'confidence': 82.5,
            'level': 'High',
            'message': 'High flood risk detected near the reported location.',
            'primary_factors': ['heavy rain', 'river rise'],
            'recommended_agencies': ['BFP'],
            'recommended_resources': ['Water 50L'],
            'alert': True,
        }

        test_user = User(username='test_coordinator', email='test@example.com', password='pass', role='COORDINATOR')
        db.session.add(test_user)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess['username'] = 'test_coordinator'
            sess['role'] = 'COORDINATOR'

        response = self.client.post('/ai-prediction', data={
            'hazard_type': 'flood',
            'rainfall': '120',
            'river_level': '3.2',
            'humidity_pct': '85',
            'population_density': '2500',
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AIRecommendation.query.count(), 1)
        self.assertEqual(AuditEvent.query.count(), 1)
        recommendation = AIRecommendation.query.first()
        audit_event = AuditEvent.query.first()
        self.assertEqual(recommendation.provider, 'openai')
        self.assertEqual(recommendation.model, 'gpt-5.6-terra')
        self.assertEqual(recommendation.recommendation_type, 'hazard_prediction')
        self.assertEqual(recommendation.confidence_score, 82.5)
        self.assertEqual(json.loads(recommendation.recommended_agencies), ['BFP'])
        self.assertEqual(json.loads(recommendation.recommended_resources), ['Water 50L'])
        self.assertEqual(json.loads(recommendation.primary_factors), ['heavy rain', 'river rise'])
        self.assertEqual(Incident.query.filter_by(reported_by='ai_prediction').one().status, 'NEW')
        self.assertEqual(audit_event.entity_type, 'AIRecommendation')
        self.assertEqual(audit_event.action, 'CREATED')

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

    def test_ai_recommended_agencies_are_validated_against_agency_table(self):
        db.session.add(Agency(name='BFP'))
        db.session.add(Agency(name='DOH'))
        db.session.commit()

        normalized = _normalize_recommended_agencies(['bfp', 'DOH', 'invalid', '  doh  '])
        self.assertEqual(normalized, ['BFP', 'DOH'])

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
        self.assertEqual(result['recommended_agencies'], [])
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

        self.assertIn('River level: unavailable (no river gauge data)', prompt)
        self.assertNotIn('River level: None m', prompt)


if __name__ == '__main__':
    unittest.main()
