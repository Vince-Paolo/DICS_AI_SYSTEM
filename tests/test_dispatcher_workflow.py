import os
import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'test-secret-key'

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from flask_babel import refresh

from app import app, db
from models import (
    Barangay,
    BarangayContactLog,
    BarangayResponseUnit,
    AIRecommendation,
    AuditEvent,
    Incident,
    IncidentResponse,
    Municipality,
    Province,
    Resource,
    ResponseTeam,
    Task,
    User,
)
from seed.demo_data import seed_geography_data
from services.response_decision_support import (
    build_response_assessment,
    sign_response_assessment,
)


class DispatcherWorkflowTestCase(unittest.TestCase):
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
        self.dispatcher = User(
            username='phase3_dispatcher',
            email='phase3-dispatcher@example.com',
            password='secret',
            role='dispatcher',
            email_verified=True,
        )
        self.responder = User(
            username='phase3_responder',
            email='phase3-responder@example.com',
            password='secret',
            role='field_responder',
            email_verified=True,
        )
        self.commander = User(
            username='phase3_commander',
            email='phase3-commander@example.com',
            password='secret',
            role='incident_commander',
            email_verified=True,
        )
        db.session.add_all([self.dispatcher, self.responder, self.commander])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        with self.client.session_transaction() as session:
            session['username'] = user.username
            session['role'] = user.role

    @staticmethod
    def _incident():
        return Incident(
            hazard_type='EMERGENCY',
            location='San Pablo City',
            message='Emergency reported by dispatcher',
            status='NEW',
        )

    def test_dispatcher_dashboard_shows_open_emergencies_and_available_responders(self):
        unassigned_incident = self._incident()
        unassigned_incident.control_number = '2026-0001'
        unassigned_incident.level = 'High'
        unassigned_incident.patient_count = 2
        unassigned_incident.created_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        )
        assigned_incident = self._incident()
        assigned_incident.control_number = '2026-0002'
        assigned_incident.level = 'Severe'
        assigned_incident.status = 'ACTIVE'
        closed_incident = self._incident()
        closed_incident.control_number = '2026-0003'
        closed_incident.status = 'CLOSED'
        previous_day_incident = self._incident()
        previous_day_incident.control_number = '2026-0004'
        previous_day_incident.level = 'High'
        previous_day_incident.created_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3, minutes=10)
        )
        free_responder = User(
            username='free_responder',
            email='free-responder@example.com',
            password='secret',
            role='field_responder',
            email_verified=True,
        )
        response = IncidentResponse(
            incident=assigned_incident,
            status='ACTIVE',
        )
        task = Task(
            incident_response=response,
            assigned_to_user_id=self.responder.id,
            assigned_by_id=self.dispatcher.id,
            title='Check affected area',
            description='Assess reported emergency',
            status='IN_PROGRESS',
        )
        db.session.add_all([
            unassigned_incident,
            assigned_incident,
            closed_incident,
            previous_day_incident,
            free_responder,
            task,
        ])
        db.session.commit()
        self._login(self.dispatcher)

        dashboard = self.client.get('/dispatcher-dashboard')

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'2026-0001', dashboard.data)
        self.assertIn(b'2026-0002', dashboard.data)
        self.assertIn(b'2026-0004', dashboard.data)
        self.assertNotIn(b'2026-0003', dashboard.data)
        self.assertLess(
            dashboard.data.index(b'2026-0002'),
            dashboard.data.index(b'2026-0004'),
        )
        self.assertLess(
            dashboard.data.index(b'2026-0004'),
            dashboard.data.index(b'2026-0001'),
        )
        self.assertIn(b'3 days ago', dashboard.data)
        self.assertIn(b'id="active-emergency-count">3<', dashboard.data)
        self.assertIn(b'id="logged-today-count">3<', dashboard.data)
        self.assertIn(b'id="unassigned-emergency-count">2<', dashboard.data)
        self.assertIn(b'All open emergencies', dashboard.data)
        self.assertIn(b'id="free-responder-count">1<', dashboard.data)
        self.assertIn(b'href="/analytics"', dashboard.data)
        self.assertIn(b'href="/hazard-map"', dashboard.data)
        self.assertIn(b'Log emergency', dashboard.data)

        self.client.get('/language/fil_PH?next=/dispatcher-dashboard')
        refresh()
        translated_dashboard = self.client.get('/dispatcher-dashboard')
        self.assertEqual(translated_dashboard.status_code, 200)
        self.assertIn('Malubha'.encode(), translated_dashboard.data)
        self.assertIn('Mataas'.encode(), translated_dashboard.data)
        self.assertIn('Aktibo'.encode(), translated_dashboard.data)
        self.assertIn('3 araw na ang nakalipas'.encode(), translated_dashboard.data)

        self.assertEqual(self.client.get('/hazard-map').status_code, 200)
        self.assertEqual(self.client.get('/analytics').status_code, 200)

    def test_dispatcher_dashboard_log_button_opens_existing_emergency_form(self):
        self._login(self.dispatcher)

        response = self.client.get('/dispatcher-dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/eoc/emergency-log?create=1#manual-emergency-location', response.data)

        emergency_log = self.client.get('/eoc/emergency-log?create=1')
        self.assertEqual(emergency_log.status_code, 200)
        self.assertIn(b'<details class="card card-custom mb-4" open>', emergency_log.data)

    def test_imt_emergencies_view_uses_active_verification_and_closed_tabs(self):
        active_incident = Incident(
            hazard_type='earthquake',
            location='San Pablo City',
            message='Verified incident',
            status='VERIFIED',
        )
        pending_emergency = self._incident()
        pending_emergency.control_number = '2026-0002'
        closed_incident = Incident(
            hazard_type='flood',
            location='San Pablo City',
            message='Resolved incident',
            status='RESOLVED',
        )
        db.session.add_all([active_incident, pending_emergency, closed_incident])
        db.session.commit()
        self._login(self.commander)

        active = self.client.get('/eoc/incidents?view=active')
        self.assertEqual(active.status_code, 200)
        self.assertIn(f'>#{active_incident.id}</a>'.encode(), active.data)
        self.assertNotIn(b'2026-0002', active.data)
        self.assertNotIn(f'>#{closed_incident.id}</a>'.encode(), active.data)
        self.assertIn(b'Verification', active.data)
        self.assertIn(b'Units and Resources', active.data)
        self.assertIn(b'href="/admin/alerts"', active.data)

        verification = self.client.get('/eoc/incidents?view=verification')
        self.assertEqual(verification.status_code, 200)
        self.assertIn(b'2026-0002', verification.data)
        self.assertNotIn(f'>#{active_incident.id}</a>'.encode(), verification.data)

        verification_queue = self.client.get('/admin/alerts')
        self.assertEqual(verification_queue.status_code, 200)
        self.assertIn(b'Reject false alarm', verification_queue.data)

        closed = self.client.get('/eoc/incidents?view=closed')
        self.assertEqual(closed.status_code, 200)
        self.assertIn(f'>#{closed_incident.id}</a>'.encode(), closed.data)
        self.assertNotIn(f'>#{active_incident.id}</a>'.encode(), closed.data)

    def test_imt_can_view_and_manage_response_teams_from_units_group(self):
        self._login(self.commander)

        response = self.client.get('/dispatcher/teams')
        self.assertEqual(response.status_code, 200)

        saved = self.client.post('/dispatcher/teams', data={
            'name': 'IMT Medical Team',
            'member_ids': [str(self.responder.id)],
        })
        self.assertEqual(saved.status_code, 302)
        team = ResponseTeam.query.filter_by(name='IMT Medical Team').one()
        self.assertEqual(team.created_by_id, self.commander.id)
        self.assertEqual([member.id for member in team.members], [self.responder.id])

    def test_dispatcher_can_log_a_manual_emergency(self):
        province = Province(code='0434', name='Laguna')
        db.session.add(province)
        db.session.flush()
        municipality = Municipality(
            code='043424',
            name='San Pablo City',
            province_id=province.id,
        )
        db.session.add(municipality)
        db.session.flush()
        barangay = Barangay(
            municipality_id=municipality.id,
            code='043424001',
            name='San Roque',
        )
        db.session.add(barangay)
        db.session.commit()

        self._login(self.dispatcher)
        response = self.client.post('/dispatcher/emergency-log/new', data={
            'location': 'Barangay San Roque',
            'description': 'Vehicle collision, medical assistance needed',
            'patient_count': '2',
            'severity': 'High',
            'barangay_id': str(barangay.id),
        })
        self.assertEqual(response.status_code, 302)
        incident = Incident.query.filter_by(hazard_type='EMERGENCY').one()
        self.assertEqual(incident.location, 'Barangay San Roque')
        self.assertEqual(incident.patient_count, 2)
        self.assertEqual(incident.level, 'High')
        self.assertEqual(incident.barangay_id, barangay.id)
        self.assertEqual(incident.reported_by, f'dispatcher:{self.dispatcher.id}')
        self.assertEqual(response.location, f'/eoc/emergency-log/{incident.id}')

    def test_only_dispatchers_can_create_manual_emergencies(self):
        self._login(self.commander)
        response = self.client.post('/dispatcher/emergency-log/new', data={
            'location': 'San Pablo City',
            'description': 'Test',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Incident.query.count(), 0)

    def test_rules_based_assessment_escalates_severe_multi_patient_incidents(self):
        incident = self._incident()
        incident.level = 'Severe'
        incident.patient_count = 10
        incident.message = 'Severe flood emergency.'
        incident.latitude = 14.0683
        incident.longitude = 121.3256

        with patch(
            'services.response_decision_support.get_rainfall_watch',
            return_value=[],
        ) as rainfall_fetch:
            assessment = build_response_assessment(incident)
        rainfall_fetch.assert_not_called()

        self.assertEqual(assessment['outcome'], 'CDRRMO_TAKEOVER')
        self.assertEqual(assessment['rules_version'], 'rules-v1')
        factors = {factor['name']: factor for factor in assessment['factors']}
        self.assertEqual(factors['Severity']['points'], 30)
        self.assertEqual(factors['Patients']['points'], 15)
        self.assertEqual(factors['Vulnerable people']['value'], 'Not separately recorded')
        self.assertIn('city-center distance', factors['Distance to response units']['basis'])
        self.assertIn('no local susceptibility map', factors['Flood or landslide exposure']['basis'])

    def test_missing_severity_or_location_withholds_outcome_without_unknown_points(self):
        incident = self._incident()
        incident.location = 'Location not confirmed'
        assessment = build_response_assessment(incident)

        self.assertIsNone(assessment['score'])
        self.assertIsNone(assessment['outcome'])
        self.assertEqual(assessment['outcome_label'], 'Insufficient information')
        factors = {factor['name']: factor for factor in assessment['factors']}
        self.assertEqual(factors['Severity']['points'], 0)
        self.assertEqual(factors['Distance to response units']['points'], 0)
        self.assertEqual(factors['Barangay response capacity']['points'], 0)
        self.assertEqual(factors['Barangay rainfall']['points'], 0)

        incomplete_cases = (
            {'level': None, 'latitude': 14.0683, 'longitude': 121.3256},
            {'level': 'High', 'latitude': None, 'longitude': None},
        )
        for values in incomplete_cases:
            with self.subTest(values=values):
                incomplete = self._incident()
                incomplete.level = values['level']
                incomplete.latitude = values['latitude']
                incomplete.longitude = values['longitude']
                result = build_response_assessment(incomplete)
                self.assertIsNone(result['outcome'])
                self.assertIsNone(result['score'])

    def test_dispatcher_can_review_assessment_and_reason_for_different_decision(self):
        incident = self._incident()
        db.session.add(incident)
        db.session.commit()
        assessment = {
            'score': 40,
            'outcome': 'BARANGAY_SUPPORT',
            'outcome_label': 'Barangay needs support',
            'factors': [
                {'name': 'Patients', 'value': '2', 'points': 5, 'basis': 'Test factor.'},
                {'name': 'Vulnerable people', 'value': 'Not separately recorded', 'points': 0, 'basis': 'Test limitation.'},
            ],
            'rules_version': 'rules-v1',
            'barangay_name': 'San Roque',
        }
        self._login(self.dispatcher)

        with patch('blueprints.eoc.build_response_assessment', return_value=assessment):
            detail = self.client.get(f'/eoc/emergency-log/{incident.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'Response decision support', detail.data)
        self.assertIn(b'Barangay needs support', detail.data)
        self.assertIn(b'Vulnerable people', detail.data)
        self.assertIn(b'name="assessment_token"', detail.data)

        higher_priority_incident = self._incident()
        db.session.add(higher_priority_incident)
        db.session.commit()
        higher_priority_assessment = {
            **assessment,
            'score': 80,
            'outcome': 'CDRRMO_TAKEOVER',
            'outcome_label': 'CDRRMO takes over',
        }
        with patch(
            'blueprints.dispatcher.build_response_assessment',
            side_effect=lambda target: (
                higher_priority_assessment if target.id == higher_priority_incident.id
                else assessment
            ),
        ):
            decision_list = self.client.get('/dispatcher/decision-support')
        self.assertEqual(decision_list.status_code, 200)
        self.assertLess(
            decision_list.data.index(higher_priority_incident.control_number.encode()),
            decision_list.data.index(incident.control_number.encode()),
        )
        self.assertIn(incident.control_number.encode(), decision_list.data)
        self.assertIn(b'Find an emergency', decision_list.data)
        self.assertIn(b'data-decision-filter="CDRRMO_TAKEOVER"', decision_list.data)
        self.assertIn(b'data-decision-filter="BARANGAY_SUPPORT"', decision_list.data)
        self.assertIn(b'Review and decide', decision_list.data)
        self.assertIn(b'How to read these recommendations', decision_list.data)

        decision_url = f'/dispatcher/incidents/{incident.id}/decision'
        assessment_token = sign_response_assessment(incident.id, assessment)
        with patch(
            'blueprints.dispatcher.build_response_assessment',
            side_effect=AssertionError('Decision submission must use the displayed snapshot'),
        ):
            missing_reason = self.client.post(decision_url, data={
                'decision': 'CDRRMO_TAKEOVER',
                'assessment_token': assessment_token,
            })
        self.assertEqual(missing_reason.status_code, 302)
        self.assertEqual(AIRecommendation.query.count(), 0)

        with patch(
            'blueprints.dispatcher.build_response_assessment',
            side_effect=AssertionError('Decision submission must use the displayed snapshot'),
        ):
            accepted = self.client.post(decision_url, data={
                'decision': 'BARANGAY_SUPPORT',
                'assessment_token': assessment_token,
            })
        self.assertEqual(accepted.status_code, 302)
        self.assertTrue(accepted.location.endswith('#barangay-contact-log'))

        with patch(
            'blueprints.dispatcher.build_response_assessment',
            side_effect=AssertionError('Decision submission must use the displayed snapshot'),
        ):
            saved = self.client.post(decision_url, data={
                'decision': 'CDRRMO_TAKEOVER',
                'decision_reason': 'Several callers report worsening conditions.',
                'assessment_token': assessment_token,
            })
        self.assertEqual(saved.status_code, 302)
        recommendation = AIRecommendation.query.filter_by(
            incident_id=incident.id,
            recommendation_type='response_decision_support',
            decision='CDRRMO_TAKEOVER',
        ).one()
        self.assertEqual(recommendation.provider, 'rules-based')
        self.assertEqual(recommendation.decision, 'CDRRMO_TAKEOVER')
        self.assertEqual(recommendation.decision_reason, 'Several callers report worsening conditions.')
        self.assertEqual(recommendation.decided_by_id, self.dispatcher.id)
        self.assertEqual(json.loads(recommendation.primary_factors), assessment['factors'])
        self.assertIn('BARANGAY_SUPPORT (40/100 response-need points)', recommendation.summary)
        self.assertEqual(
            AIRecommendation.query.filter_by(
                incident_id=incident.id,
                recommendation_type='response_decision_support',
            ).count(),
            2,
        )
        self.assertEqual(AuditEvent.query.filter_by(action='RESPONSE_DECISION_RECORDED').count(), 2)

        with patch('blueprints.eoc.build_response_assessment', return_value=assessment):
            history = self.client.get(f'/eoc/emergency-log/{incident.id}')
        self.assertIn(b'Several callers report worsening conditions.', history.data)

    def test_decision_support_includes_only_san_pablo_system_hazards(self):
        weather_incident = Incident(
            hazard_type='flood',
            location='San Pablo',
            message='High rainfall risk detected.',
            reported_by='system',
            status='NEW',
        )
        regional_incident = Incident(
            hazard_type='flood',
            location='Batangas City',
            message='Regional context only.',
            reported_by='system',
            status='NEW',
        )
        db.session.add_all([weather_incident, regional_incident])
        db.session.commit()
        assessment = {
            'score': 30,
            'outcome': 'BARANGAY_SUPPORT',
            'outcome_label': 'Barangay needs support',
            'factors': [],
            'rules_version': 'rules-v1',
            'barangay_name': None,
            'barangay_unit_id': None,
        }
        self._login(self.dispatcher)

        with patch('blueprints.dispatcher.build_response_assessment', return_value=assessment):
            listing = self.client.get('/dispatcher/decision-support')
        self.assertIn(weather_incident.control_number.encode(), listing.data)
        self.assertNotIn(regional_incident.control_number.encode(), listing.data)

        with patch('blueprints.eoc.build_response_assessment', return_value=assessment):
            detail = self.client.get(f'/eoc/emergency-log/{weather_incident.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b'id="barangay-contact-log"', detail.data)
        self.assertIn(b'Log contact', detail.data)
        self.assertNotIn(b'Patient count', detail.data)
        self.assertNotIn(b'Dispatch response', detail.data)

        token = sign_response_assessment(weather_incident.id, assessment)
        with patch(
            'blueprints.dispatcher.build_response_assessment',
            side_effect=AssertionError('Decision submission must use the displayed snapshot'),
        ):
            decision = self.client.post(
                f'/dispatcher/incidents/{weather_incident.id}/decision',
                data={
                    'decision': 'BARANGAY_SUPPORT',
                    'assessment_token': token,
                },
            )
        self.assertTrue(decision.location.endswith('#barangay-contact-log'))

    def test_decision_submission_rejects_invalid_assessment_snapshot(self):
        incident = self._incident()
        db.session.add(incident)
        db.session.commit()
        self._login(self.dispatcher)

        response = self.client.post(
            f'/dispatcher/incidents/{incident.id}/decision',
            data={
                'decision': 'BARANGAY_MONITOR',
                'assessment_token': 'tampered-assessment-token',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(AIRecommendation.query.count(), 0)

    def test_only_dispatchers_can_record_final_response_decision(self):
        incident = self._incident()
        db.session.add(incident)
        db.session.commit()
        self._login(self.commander)

        response = self.client.post(
            f'/dispatcher/incidents/{incident.id}/decision',
            data={'decision': 'BARANGAY_MONITOR'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(AIRecommendation.query.count(), 0)

    def test_dispatcher_can_manage_team_roster_and_dispatch_optional_vehicle(self):
        incident = self._incident()
        db.session.add(incident)
        db.session.commit()
        incident_id = incident.id

        self._login(self.dispatcher)
        team_response = self.client.post('/dispatcher/teams', data={
            'name': 'Rescue Alpha',
            'member_ids': [str(self.responder.id)],
        })
        self.assertEqual(team_response.status_code, 302)
        team = ResponseTeam.query.filter_by(name='Rescue Alpha').one()
        self.assertEqual([member.id for member in team.members], [self.responder.id])
        self.assertEqual(self.client.get('/dispatcher/teams').status_code, 200)

        dispatch_response = self.client.post(
            f'/dispatcher/incidents/{incident_id}/dispatch',
            data={
                'title': 'Initial rescue sweep',
                'description': 'Search the designated block and report findings.',
                'assignee': f'team:{team.id}',
                'priority': 'HIGH',
                'vehicle_identifier': 'RESCUE-01',
                'vehicle_location': 'Central staging point',
            },
        )
        self.assertEqual(dispatch_response.status_code, 302)
        incident_response = IncidentResponse.query.filter_by(incident_id=incident_id).one()
        self.assertEqual(incident_response.dispatched_by_id, self.dispatcher.id)
        self.assertIsNone(incident_response.commander_id)
        task = Task.query.filter_by(incident_response_id=incident_response.id).one()
        self.assertEqual(task.assigned_to_team_id, team.id)
        self.assertIsNone(task.assigned_to_user_id)
        self.assertFalse(hasattr(task, 'assigned_to_agency'))
        vehicle = Resource.query.filter_by(incident_response_id=incident_response.id).one()
        self.assertEqual(vehicle.resource_type, 'Vehicles')
        self.assertEqual(vehicle.notes, 'RESCUE-01')
        self.assertEqual(vehicle.location, 'Central staging point')

        detail_response = self.client.get(f'/eoc/emergency-log/{incident_id}')
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn('Initial rescue sweep', detail_response.get_data(as_text=True))
        person_dispatch = self.client.post(
            f'/dispatcher/incidents/{incident_id}/dispatch',
            data={
                'title': 'Medical follow-up',
                'description': 'Provide medical support at the staging area.',
                'assignee': f'user:{self.responder.id}',
            },
        )
        self.assertEqual(person_dispatch.status_code, 302)
        person_task = Task.query.filter_by(title='Medical follow-up').one()
        self.assertEqual(person_task.assigned_to_user_id, self.responder.id)
        self.assertIsNone(person_task.assigned_to_team_id)

        self._login(self.responder)
        tasks_response = self.client.get('/responder-tasks')
        self.assertEqual(tasks_response.status_code, 200)
        self.assertIn('Initial rescue sweep', tasks_response.get_data(as_text=True))
        self.assertIn('Medical follow-up', tasks_response.get_data(as_text=True))

    def test_geography_seed_creates_san_pablo_barangay_response_unit_directory(self):
        seed_geography_data()
        municipality = Municipality.query.filter_by(code='043424').one()
        self.assertEqual(
            BarangayResponseUnit.query.join(Barangay).filter(
                Barangay.municipality_id == municipality.id
            ).count(),
            80,
        )

    def test_dispatcher_can_update_city_directory_and_log_incident_contact(self):
        province = Province(name='Laguna')
        db.session.add(province)
        db.session.flush()
        municipality = Municipality(province_id=province.id, code='043424', name='San Pablo City')
        db.session.add(municipality)
        db.session.flush()
        barangay = Barangay(municipality_id=municipality.id, code='043424001', name='San Roque')
        db.session.add(barangay)
        db.session.flush()
        unit = BarangayResponseUnit(barangay_id=barangay.id)
        other_barangay = Barangay(
            municipality_id=municipality.id,
            code='043424002',
            name='Zaragoza',
        )
        db.session.add(other_barangay)
        db.session.flush()
        other_unit = BarangayResponseUnit(barangay_id=other_barangay.id)
        incident = self._incident()
        db.session.add_all([unit, other_unit, incident])
        db.session.commit()
        unit_id, other_unit_id, incident_id = unit.id, other_unit.id, incident.id

        self._login(self.dispatcher)
        directory_response = self.client.get(f'/dispatcher/barangay-units?unit={unit_id}')
        self.assertEqual(directory_response.status_code, 200)
        directory_html = directory_response.get_data(as_text=True)
        self.assertIn('San Roque', directory_html)
        self.assertIn('Zaragoza', directory_html)
        self.assertIn('Search barangay or code', directory_html)
        self.assertIn('barangay-directory barangay-directory-panel', directory_html)
        self.assertIn('align-items: stretch', directory_html)
        self.assertIn('height: 100%', directory_html)
        self.assertIn('max-height: calc(100vh - 2rem)', directory_html)
        self.assertIn('overflow-y: auto', directory_html)
        self.assertIn('Needs information', directory_html)
        self.assertIn('Recently updated', directory_html)
        self.assertIn('class="barangay-code"', directory_html)
        self.assertEqual(directory_html.count('name="contact_person"'), 1)
        self.assertIn(f'id="contact-person-{unit_id}"', directory_html)
        self.assertNotIn(f'id="contact-person-{other_unit_id}"', directory_html)

        selected_directory = self.client.get(
            f'/dispatcher/barangay-units?unit={other_unit_id}&q=Zar&filter=needs-info'
        )
        selected_html = selected_directory.get_data(as_text=True)
        self.assertEqual(selected_directory.status_code, 200)
        self.assertIn(f'id="contact-person-{other_unit_id}"', selected_html)
        self.assertNotIn(f'id="contact-person-{unit_id}"', selected_html)
        self.assertIn('Back to barangays', selected_html)
        self.assertIn('value="Zar"', selected_html)

        update_response = self.client.post(f'/dispatcher/barangay-units/{unit_id}', data={
            'contact_person': 'Barangay Operations Desk',
            'contact_number': '049-123-4567',
            'team_size': '8',
            'equipment': 'Rescue boat',
            'location': 'Barangay hall',
            'q': 'San',
            'filter': 'needs-info',
        })
        self.assertEqual(update_response.status_code, 302)
        self.assertIn(f'unit={unit_id}', update_response.location)
        self.assertIn('q=San', update_response.location)
        self.assertIn('filter=needs-info', update_response.location)
        self.assertEqual(unit.contact_person, 'Barangay Operations Desk')
        self.assertEqual(unit.team_size, 8)
        profile_response = self.client.get(
            f'/dispatcher/barangay-units?unit={unit_id}'
        )
        profile_html = profile_response.get_data(as_text=True)
        self.assertEqual(profile_response.status_code, 200)
        self.assertIn('Response profile', profile_html)
        self.assertIn('8 responders', profile_html)
        self.assertIn('Barangay hall', profile_html)
        self.assertIn('Rescue boat', profile_html)
        self.assertIn('href="tel:0491234567"', profile_html)
        self.assertIn('Call 049-123-4567', profile_html)

        contact_response = self.client.post(
            f'/dispatcher/incidents/{incident_id}/barangay-contacts',
            data={
                'response_unit_id': str(unit_id),
                'contact_method': 'RADIO',
                'outcome': 'ASSISTANCE_DISPATCHED',
                'contacted_person': 'Duty officer',
                'notes': 'Two responders are en route.',
            },
        )
        self.assertEqual(contact_response.status_code, 302)
        contact = BarangayContactLog.query.filter_by(incident_id=incident_id).one()
        self.assertEqual(contact.response_unit_id, unit_id)
        self.assertEqual(contact.logged_by_id, self.dispatcher.id)
        detail = self.client.get(f'/eoc/emergency-log/{incident_id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('Two responders are en route.', detail.get_data(as_text=True))
        directory_with_activity = self.client.get(
            f'/dispatcher/barangay-units?unit={unit_id}'
        )
        self.assertEqual(directory_with_activity.status_code, 200)
        activity_html = directory_with_activity.get_data(as_text=True)
        self.assertIn('Recent barangay activity', activity_html)
        self.assertIn(incident.control_number, activity_html)
        self.assertIn('Duty officer', activity_html)
        self.assertIn('Radio', activity_html)
        self.assertIn('Assistance Dispatched', activity_html)
        self.assertIn('Two responders are en route.', activity_html)

    def test_commander_has_read_only_dispatcher_oversight(self):
        incident = self._incident()
        db.session.add(incident)
        db.session.flush()
        response = IncidentResponse(
            incident_id=incident.id,
            commander_id=None,
            dispatched_by_id=self.dispatcher.id,
            status='ACTIVE',
        )
        db.session.add(response)
        db.session.flush()
        task = Task(
            incident_response_id=response.id,
            assigned_to_user_id=self.responder.id,
            assigned_by_id=self.dispatcher.id,
            title='Read-only oversight task',
            description='Inspect the affected area.',
            status='PENDING',
        )
        db.session.add(task)
        db.session.commit()
        response_id = response.id

        self._login(self.commander)
        oversight = self.client.get('/commander/dispatcher-operations')
        self.assertEqual(oversight.status_code, 200)
        self.assertIn('Read-only oversight task', oversight.get_data(as_text=True))
        self.assertEqual(
            self.client.post(
                f'/dispatcher/incidents/{response.incident_id}/dispatch',
                data={'title': 'Not allowed', 'description': 'Test'},
            ).status_code,
            403,
        )
        self.assertEqual(Task.query.count(), 1)

    def test_dispatcher_migration_adds_schema_and_supports_clean_downgrade(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / 'migrations'
            / 'versions'
            / 'e3b2d7a4c891_add_dispatcher_teams_and_barangay_contacts.py'
        )
        spec = importlib.util.spec_from_file_location('dispatcher_migration', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.connect() as connection:
            connection.connection.driver_connection.execute('PRAGMA foreign_keys = OFF')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE incident (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql(
                'CREATE TABLE incident_response (id INTEGER PRIMARY KEY, '
                'incident_id INTEGER NOT NULL REFERENCES incident(id), '
                'commander_id INTEGER NOT NULL REFERENCES "user"(id))'
            )
            connection.exec_driver_sql(
                'CREATE TABLE task (id INTEGER PRIMARY KEY, '
                'incident_response_id INTEGER NOT NULL REFERENCES incident_response(id), '
                'assigned_to_agency VARCHAR(150) NOT NULL, '
                'assigned_by_id INTEGER NOT NULL REFERENCES "user"(id))'
            )
            connection.exec_driver_sql('CREATE TABLE barangay (id INTEGER PRIMARY KEY)')
            connection.execute(text('INSERT INTO "user" (id) VALUES (1)'))
            connection.execute(text('INSERT INTO incident (id) VALUES (1)'))
            connection.execute(text(
                'INSERT INTO incident_response (id, incident_id, commander_id) VALUES (1, 1, 1)'
            ))
            connection.execute(text(
                "INSERT INTO task (id, incident_response_id, assigned_to_agency, assigned_by_id) "
                "VALUES (1, 1, 'Legacy Agency', 1)"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            response_columns = {
                column['name']: column for column in inspect(connection).get_columns('incident_response')
            }
            task_columns = {
                column['name']: column for column in inspect(connection).get_columns('task')
            }
            self.assertIn('dispatched_by_id', response_columns)
            self.assertTrue(response_columns['commander_id']['nullable'])
            self.assertIn('assigned_to_user_id', task_columns)
            self.assertIn('assigned_to_team_id', task_columns)
            self.assertTrue(task_columns['assigned_to_agency']['nullable'])
            self.assertIn('barangay_response_unit', inspect(connection).get_table_names())
            self.assertIn('barangay_contact_log', inspect(connection).get_table_names())

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            task_columns = {column['name'] for column in inspect(connection).get_columns('task')}
            response_columns = {
                column['name']: column for column in inspect(connection).get_columns('incident_response')
            }
            self.assertNotIn('assigned_to_user_id', task_columns)
            self.assertNotIn('assigned_to_team_id', task_columns)
            self.assertFalse(response_columns['commander_id']['nullable'])
            self.assertNotIn('barangay_response_unit', inspect(connection).get_table_names())
        engine.dispose()

    def test_dispatcher_migration_refuses_downgrade_with_new_assignments(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / 'migrations'
            / 'versions'
            / 'e3b2d7a4c891_add_dispatcher_teams_and_barangay_contacts.py'
        )
        spec = importlib.util.spec_from_file_location('dispatcher_migration_guard', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.connect() as connection:
            connection.connection.driver_connection.execute('PRAGMA foreign_keys = OFF')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql('CREATE TABLE incident (id INTEGER PRIMARY KEY)')
            connection.exec_driver_sql(
                'CREATE TABLE incident_response (id INTEGER PRIMARY KEY, '
                'incident_id INTEGER NOT NULL REFERENCES incident(id), '
                'commander_id INTEGER NOT NULL REFERENCES "user"(id))'
            )
            connection.exec_driver_sql(
                'CREATE TABLE task (id INTEGER PRIMARY KEY, '
                'incident_response_id INTEGER NOT NULL REFERENCES incident_response(id), '
                'assigned_to_agency VARCHAR(150) NOT NULL, '
                'assigned_by_id INTEGER NOT NULL REFERENCES "user"(id))'
            )
            connection.exec_driver_sql('CREATE TABLE barangay (id INTEGER PRIMARY KEY)')
            connection.execute(text('INSERT INTO "user" (id) VALUES (1)'))
            connection.execute(text('INSERT INTO incident (id) VALUES (1)'))
            connection.execute(text(
                'INSERT INTO incident_response (id, incident_id, commander_id) VALUES (1, 1, 1)'
            ))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text(
                'UPDATE incident_response SET commander_id = NULL, dispatched_by_id = 1 WHERE id = 1'
            ))
            connection.execute(text(
                "INSERT INTO task (id, incident_response_id, assigned_to_agency, assigned_by_id, "
                "assigned_to_user_id) VALUES (1, 1, NULL, 1, 1)"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                with self.assertRaisesRegex(RuntimeError, 'tasks are assigned'):
                    migration.downgrade()
            self.assertIn(
                'assigned_to_user_id',
                {column['name'] for column in inspect(connection).get_columns('task')},
            )
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
