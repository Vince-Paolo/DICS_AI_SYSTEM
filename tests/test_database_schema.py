import json
import unittest
from unittest.mock import patch

from app import app, db
from models import (
    Agency,
    Alert,
    AIRecommendation,
    AuditEvent,
    Barangay,
    EvacuationCenter,
    Facility,
    Incident,
    IncidentReport,
    Message,
    Municipality,
    Province,
    Report,
    Resource,
    ResourceRequest,
    Task,
    User,
)
from ai.decision_support import _normalize_recommended_agencies, _deterministic_low_risk_exit


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
        self.assertTrue(Message.__tablename__)
        self.assertTrue(Province.__tablename__)
        self.assertTrue(Municipality.__tablename__)
        self.assertTrue(Barangay.__tablename__)
        self.assertTrue(Facility.__tablename__)
        self.assertTrue(EvacuationCenter.__tablename__)
        self.assertTrue(IncidentReport.__tablename__)
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
            'level': 'HIGH',
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
        self.assertEqual(audit_event.entity_type, 'AIRecommendation')
        self.assertEqual(audit_event.action, 'CREATED')

    def test_incident_tracks_external_event_id_for_scheduler_deduplication(self):
        self.assertIn('external_event_id', Incident.__table__.columns.keys())
        self.assertIsNotNone(Incident.__table__.c.external_event_id)

    def test_ai_recommended_agencies_are_validated_against_agency_table(self):
        db.session.add(Agency(name='BFP'))
        db.session.add(Agency(name='DOH'))
        db.session.commit()

        normalized = _normalize_recommended_agencies(['bfp', 'DOH', 'invalid', '  doh  '])
        self.assertEqual(normalized, ['BFP', 'DOH'])

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


if __name__ == '__main__':
    unittest.main()
