import os
import unittest

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
# See tests/test_responder_routes.py for why this must be set before `app` is imported.
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_operational_data_features.db'))
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

from app import app, db
from models import (
    Alert,
    AIRecommendation,
    EvacuationCenter,
    EvacuationRecord,
    Facility,
    Incident,
    IncidentMessage,
    IncidentResponse,
    Barangay,
    Municipality,
    Province,
    Report,
    Resource,
    Task,
    User,
)
from seed.demo_data import seed_geography_data


class OperationalDataFeaturesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            seed_geography_data()

            for uname, role in [
                ('admin1', 'admin'),
                ('eoc1', 'imt_staff'),
                ('cmd1', 'imt_staff'),
                ('cmd2', 'imt_staff'),
                ('cit1', 'citizen'),
            ]:
                db.session.add(User(
                    username=uname, email=f'{uname}@example.com', password='secret',
                    role=role, email_verified=True,
                ))
            db.session.add(Incident(
                hazard_type='flood', location='Test Barangay', level='High', score=70.0,
                message='test incident', status='REPORTED', alert=True,
            ))
            db.session.commit()
            self.incident_id = Incident.query.first().id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def _login(self, username, role):
        with self.client.session_transaction() as sess:
            sess['username'] = username
            sess['role'] = role

    def test_verification_queue_search_and_compact_actions_render(self):
        self._login('eoc1', 'eoc_staff')
        response = self.client.get('/admin/alerts')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/eoc/incidents?view=active"', html)
        self.assertIn('href="/admin/alerts"', html)
        self.assertIn('href="/eoc/incidents?view=closed"', html)
        self.assertIn('id="verificationSearch"', html)
        self.assertIn('data-search="flood test barangay test incident', html)
        self.assertIn('data-bs-target="#rejectIncidentModal1"', html)
        self.assertIn('id="rejectIncidentModal1"', html)
        self.assertIn('id="rejectReason1"', html)
        self.assertIn('id="commander_id_1"', html)
        self.assertIn('Confirm rejection', html)

    # -- Facility / EvacuationCenter -----------------------------------

    def test_admin_cannot_add_facility(self):
        """Admin's role is pure administration, not incident operations --
        adding to the facility directory is now EOC-only (see
        can_manage_facilities in services/permissions.py)."""
        self._login('admin1', 'admin')
        resp = self.client.post('/facilities/add', data={
            'name': 'Test Gym', 'facility_type': 'Evacuation Center', 'capacity': '200',
        })
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Facility.query.filter_by(name='Test Gym').first())

    def test_eoc_can_add_facility(self):
        self._login('eoc1', 'eoc_staff')
        resp = self.client.post('/facilities/add', data={
            'name': 'Test Gym', 'facility_type': 'Evacuation Center', 'capacity': '200',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            facility = Facility.query.filter_by(name='Test Gym').first()
            self.assertIsNotNone(facility)
            self.assertIsNotNone(facility.evacuation_center)
            self.assertEqual(facility.evacuation_center.capacity, 200)
            self.assertEqual(facility.evacuation_center.status, 'OPEN')

    def test_facility_form_uses_dependent_location_dropdowns(self):
        self._login('eoc1', 'eoc_staff')
        response = self.client.get('/facilities')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="facility_province_id"', html)
        self.assertIn('id="facility_municipality_id"', html)
        self.assertIn('id="facility_barangay_id"', html)
        self.assertIn("document.querySelectorAll('main .modal')", html)
        self.assertIn('document.body.appendChild(modal)', html)
        self.assertIn("/api/municipalities/", html)
        self.assertIn("/api/barangays/", html)
        self.assertNotIn('name="municipality_id" class="form-select form-select-sm" disabled>\n                                <option value="">—</option>', html)

    def test_facility_location_hierarchy_is_validated_on_submit(self):
        self._login('eoc1', 'eoc_staff')
        with self.app.app_context():
            province = Province.query.first()
            other_province = Province(name='Out-of-scope Province', code='9999')
            db.session.add(other_province)
            db.session.commit()
            municipality = Municipality.query.filter_by(province_id=province.id).first()
            barangay = Barangay.query.filter_by(municipality_id=municipality.id).first()
            other_province_id = other_province.id
            municipality_id = municipality.id
            barangay_id = barangay.id

        response = self.client.post('/facilities/add', data={
            'name': 'Invalid Location Facility',
            'facility_type': 'Hospital',
            'province_id': other_province_id,
            'municipality_id': municipality_id,
            'barangay_id': barangay_id,
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Facility.query.filter_by(name='Invalid Location Facility').first())

    def test_admin_cannot_view_facilities_page(self):
        self._login('admin1', 'admin')
        resp = self.client.get('/facilities', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_commander_can_still_view_facilities_page(self):
        """Regression guard: only Admin's access was removed. Commander and
        Coordinator keep their existing read access to the facility
        directory (e.g. checking evacuation center capacity in the field)."""
        self._login('cmd1', 'incident_commander')
        resp = self.client.get('/facilities', follow_redirects=False)
        self.assertEqual(resp.status_code, 200)

    def test_citizen_cannot_add_facility(self):
        self._login('cit1', 'citizen')
        resp = self.client.post('/facilities/add', data={
            'name': 'Should Not Exist', 'facility_type': 'Hospital',
        })
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Facility.query.filter_by(name='Should Not Exist').first())

    def test_eoc_can_update_evacuation_center_status_without_editing_occupancy(self):
        with self.app.app_context():
            facility = Facility(name='Center A', facility_type='Evacuation Center')
            db.session.add(facility)
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=0, status='OPEN')
            db.session.add(center)
            db.session.commit()
            center_id = center.id

        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/evacuation-centers/{center_id}/update', data={
            'occupancy': '85', 'status': 'FULL',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            updated = db.session.get(EvacuationCenter, center_id)
            self.assertEqual(updated.occupancy, 0)
            self.assertEqual(updated.status, 'FULL')

    def test_incident_runs_through_task_resource_evacuation_alert_and_resolution(self):
        with self.app.app_context():
            incident = db.session.get(Incident, self.incident_id)
            incident.hazard_type = 'EMERGENCY'
            db.session.add(AIRecommendation(
                incident_id=incident.id,
                provider='test',
                model='test-model',
                recommendation_type='hazard_prediction',
                summary='High flood risk requires coordinated response.',
            ))
            dispatcher = User(
                username='dispatch1',
                email='dispatch1@example.com',
                password='secret',
                role='dispatcher',
                email_verified=True,
            )
            responder = User(
                username='responder1',
                email='responder1@example.com',
                password='secret',
                role='field_responder',
                email_verified=True,
            )
            db.session.add_all([dispatcher, responder])
            db.session.commit()
            responder_id = responder.id

        self._login('cmd1', 'incident_commander')
        activated = self.client.post(
            f'/incident/{self.incident_id}/activate-response',
            data={'decision': 'ACCEPTED'},
        )
        self.assertEqual(activated.status_code, 302)
        with self.app.app_context():
            response = IncidentResponse.query.filter_by(incident_id=self.incident_id).one()
            response_id = response.id

        self._login('dispatch1', 'dispatcher')
        assigned = self.client.post(f'/dispatcher/incidents/{self.incident_id}/dispatch', data={
            'assignee': f'user:{responder_id}',
            'title': 'Secure evacuation route',
            'description': 'Keep the route clear for evacuees.',
            'priority': 'HIGH',
            'vehicle_identifier': 'CDRRMO-RESCUE-01',
            'vehicle_location': 'Evacuation route',
        })
        self.assertEqual(assigned.status_code, 302)
        with self.app.app_context():
            task_id = Task.query.filter_by(incident_response_id=response_id).one().id
            resource_id = Resource.query.filter_by(incident_response_id=response_id).one().id

        self._login('cmd1', 'incident_commander')
        completed = self.client.post(
            f'/incident-response/{response_id}/update-task/{task_id}',
            data={'status': 'COMPLETED'},
        )
        self.assertEqual(completed.status_code, 302)

        deployed = self.client.post(
            f'/incident-response/{response_id}/update-resource/{resource_id}',
            data={'status': 'DEPLOYED', 'location': 'Evacuation route'},
        )
        self.assertEqual(deployed.status_code, 302)

        self._login('eoc1', 'eoc_staff')
        created_center = self.client.post('/facilities/add', data={
            'name': 'Incident Shelter',
            'facility_type': 'Evacuation Center',
            'capacity': '200',
        })
        self.assertEqual(created_center.status_code, 302)
        with self.app.app_context():
            center = EvacuationCenter.query.join(EvacuationCenter.facility).filter(Facility.name == 'Incident Shelter').one()
            center_id = center.id
        self._login('cmd1', 'incident_commander')
        recorded_evacuation = self.client.post(
            f'/incident-response/{response_id}/evacuate',
            data={'evacuation_center_id': str(center_id), 'people_count': '80'},
        )
        self.assertEqual(recorded_evacuation.status_code, 302)

        issued = self.client.post('/eoc/alerts/issue', data={
            'incident_id': str(self.incident_id),
            'title': 'Flood evacuation advisory',
            'message': 'Evacuate through the secured route to Incident Shelter.',
            'severity': 'HIGH',
        })
        self.assertEqual(issued.status_code, 302)
        with self.app.app_context():
            alert_id = Alert.query.filter_by(incident_id=self.incident_id).one().id
        resolved_alert = self.client.post(f'/eoc/alerts/{alert_id}/resolve')
        self.assertEqual(resolved_alert.status_code, 302)

        closed = self.client.post(f'/incident-response/{response_id}/close', data={
            'notes': 'Evacuation completed and route secured.',
            'casualties': '0',
        })
        self.assertEqual(closed.status_code, 302)

        with self.app.app_context():
            incident = db.session.get(Incident, self.incident_id)
            response = db.session.get(IncidentResponse, response_id)
            task = db.session.get(Task, task_id)
            resource = db.session.get(Resource, resource_id)
            center = db.session.get(EvacuationCenter, center_id)
            alert = db.session.get(Alert, alert_id)
            self.assertEqual(task.status, 'COMPLETED')
            self.assertEqual(resource.status, 'DEPLOYED')
            self.assertEqual(center.occupancy, 80)
            self.assertEqual(alert.status, 'RESOLVED')
            self.assertEqual(response.status, 'CLOSED')
            self.assertFalse(incident.alert)

    def test_eoc_cannot_update_occupancy_above_capacity(self):
        with self.app.app_context():
            facility = Facility(name='Capacity Center', facility_type='Evacuation Center')
            db.session.add(facility)
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=85, status='OPEN')
            db.session.add(center)
            db.session.commit()
            center_id = center.id

        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/evacuation-centers/{center_id}/update', data={
            'occupancy': '101', 'status': 'OPEN',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            updated = db.session.get(EvacuationCenter, center_id)
            self.assertEqual(updated.occupancy, 85)
            self.assertEqual(updated.status, 'OPEN')

    def test_eoc_cannot_set_negative_occupancy(self):
        with self.app.app_context():
            facility = Facility(name='Nonnegative Center', facility_type='Evacuation Center')
            db.session.add(facility)
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=0, status='OPEN')
            db.session.add(center)
            db.session.commit()
            center_id = center.id

        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/evacuation-centers/{center_id}/update', data={
            'occupancy': '-1', 'status': 'OPEN',
        }, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_commander_evacuation_record_updates_center_occupancy(self):
        """This is the actual fix: recording an evacuation through the response
        is what moves EvacuationCenter.occupancy now, not a disconnected
        manual facility-management action."""
        with self.app.app_context():
            commander = User.query.filter_by(username='cmd1').one()
            response = IncidentResponse(incident_id=self.incident_id, commander_id=commander.id, status='ACTIVE')
            facility = Facility(name='Linked Shelter', facility_type='Evacuation Center')
            db.session.add_all([response, facility])
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=0, status='OPEN')
            db.session.add(center)
            db.session.commit()
            response_id = response.id
            center_id = center.id

        self._login('cmd1', 'incident_commander')
        resp = self.client.post(f'/incident-response/{response_id}/evacuate', data={
            'evacuation_center_id': str(center_id), 'people_count': '40',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            center = db.session.get(EvacuationCenter, center_id)
            self.assertEqual(center.occupancy, 40)
            record = EvacuationRecord.query.filter_by(incident_response_id=response_id).first()
            self.assertIsNotNone(record)
            self.assertEqual(record.people_count, 40)
            self.assertEqual(record.evacuation_center_id, center_id)

    def test_commander_evacuation_record_rejects_overfill(self):
        with self.app.app_context():
            commander = User.query.filter_by(username='cmd1').one()
            response = IncidentResponse(incident_id=self.incident_id, commander_id=commander.id, status='ACTIVE')
            facility = Facility(name='Small Shelter', facility_type='Evacuation Center')
            db.session.add_all([response, facility])
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=50, occupancy=40, status='OPEN')
            db.session.add(center)
            db.session.commit()
            response_id = response.id
            center_id = center.id

        self._login('cmd1', 'incident_commander')
        resp = self.client.post(f'/incident-response/{response_id}/evacuate', data={
            'evacuation_center_id': str(center_id), 'people_count': '20',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            center = db.session.get(EvacuationCenter, center_id)
            self.assertEqual(center.occupancy, 40)
            self.assertEqual(EvacuationRecord.query.filter_by(incident_response_id=response_id).count(), 0)

    def test_closure_auto_computes_evacuated_total_from_records(self):
        """The evacuated figure at closure now defaults to the real tracked
        total instead of a number the commander has to remember and type."""
        with self.app.app_context():
            commander = User.query.filter_by(username='cmd1').one()
            response = IncidentResponse(incident_id=self.incident_id, commander_id=commander.id, status='ACTIVE')
            facility = Facility(name='Auto Total Shelter', facility_type='Evacuation Center')
            db.session.add_all([response, facility])
            db.session.flush()
            center = EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=0, status='OPEN')
            db.session.add(center)
            db.session.commit()
            response_id = response.id
            center_id = center.id

        self._login('cmd1', 'incident_commander')
        self.client.post(f'/incident-response/{response_id}/evacuate', data={
            'evacuation_center_id': str(center_id), 'people_count': '25',
        })
        self.client.post(f'/incident-response/{response_id}/evacuate', data={
            'evacuation_center_id': str(center_id), 'people_count': '15',
        })

        # Note: no 'evacuated' field submitted at all -- it should be
        # computed from the two EvacuationRecord entries above (25 + 15 = 40).
        closed = self.client.post(f'/incident-response/{response_id}/close', data={
            'notes': 'Done.', 'casualties': '0',
        })
        self.assertEqual(closed.status_code, 302)

        with self.app.app_context():
            response = db.session.get(IncidentResponse, response_id)
            incident = db.session.get(Incident, self.incident_id)
            self.assertIn('Total Evacuated: 40', response.situation_summary)
            self.assertEqual(incident.status, 'RESOLVED')

    def test_citizen_evacuation_centers_page_shows_seeded_center(self):
        with self.app.app_context():
            facility = Facility(name='Public Center', facility_type='Evacuation Center')
            db.session.add(facility)
            db.session.flush()
            db.session.add(EvacuationCenter(facility_id=facility.id, capacity=50, occupancy=10, status='OPEN'))
            db.session.commit()

        self._login('cit1', 'citizen')
        resp = self.client.get('/citizen-evacuation-centers')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Public Center', resp.data)

    def test_incident_verification_assigns_imt_staff_as_response_lead(self):
        with self.app.app_context():
            imt_user = User.query.filter_by(username='cmd1').one()
            imt_user_id = imt_user.id

        self._login('eoc1', 'imt_staff')
        response = self.client.post(
            f'/admin/incidents/{self.incident_id}/assign-commander',
            data={'commander_id': str(imt_user_id)},
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            incident_response = IncidentResponse.query.filter_by(
                incident_id=self.incident_id,
            ).one()
            self.assertEqual(incident_response.commander_id, imt_user_id)

    def test_assigned_commander_can_reopen_closed_response(self):
        with self.app.app_context():
            response = IncidentResponse(
                incident_id=self.incident_id, commander_id=User.query.filter_by(username='cmd1').one().id,
                status='ACTIVE',
            )
            db.session.add(response)
            db.session.commit()
            response_id = response.id

        self._login('cmd1', 'incident_commander')
        close_response = self.client.post(
            f'/incident-response/{response_id}/close',
            data={'notes': 'Temporary resolution', 'casualties': '0', 'evacuated': '0'},
        )
        self.assertEqual(close_response.status_code, 302)

        reopen_response = self.client.post(f'/incident-response/{response_id}/reopen')
        self.assertEqual(reopen_response.status_code, 302)

        with self.app.app_context():
            reopened = db.session.get(IncidentResponse, response_id)
            self.assertEqual(reopened.status, 'ACTIVE')
            self.assertIsNone(reopened.closed_at)
            self.assertIsNone(reopened.resolved_at)
            self.assertTrue(reopened.incident.alert)
            self.assertTrue(IncidentMessage.query.filter_by(
                incident_response_id=response_id,
                title='Incident Response Reopened',
            ).count())

    def test_imt_staff_can_reopen_any_closed_response(self):
        with self.app.app_context():
            response = IncidentResponse(
                incident_id=self.incident_id, commander_id=User.query.filter_by(username='cmd1').one().id,
                status='CLOSED',
            )
            db.session.add(response)
            db.session.commit()
            response_id = response.id

        self._login('cmd2', 'incident_commander')
        response = self.client.post(f'/incident-response/{response_id}/reopen')
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(IncidentResponse, response_id).status, 'ACTIVE')

    # -- Alert --------------------------------------------------------

    def test_eoc_issue_alert_appears_on_citizen_alerts_page(self):
        self._login('eoc1', 'eoc_staff')
        resp = self.client.post('/eoc/alerts/issue', data={
            'title': 'Flood Warning', 'message': 'Rising water.', 'severity': 'HIGH',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            alert = Alert.query.first()
            self.assertIsNotNone(alert)
            self.assertEqual(alert.status, 'ACTIVE')
            alert_id = alert.id

        self._login('cit1', 'citizen')
        resp = self.client.get('/citizen-alerts')
        self.assertIn(b'Flood Warning', resp.data)

        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/eoc/alerts/{alert_id}/resolve', data={})
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            resolved = db.session.get(Alert, alert_id)
            self.assertEqual(resolved.status, 'RESOLVED')

    def test_coordinator_cannot_issue_alert(self):
        self._login('coord1', 'agency_coordinator')
        resp = self.client.post('/eoc/alerts/issue', data={
            'title': 'Should not publish', 'message': 'x', 'severity': 'LOW',
        })
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Alert.query.filter_by(title='Should not publish').first())

    def test_admin_cannot_issue_alert(self):
        """Admin's role is pure administration, not incident operations --
        publishing official citizen-facing alerts is now EOC/Commander-only
        (see can_issue_alert in services/permissions.py)."""
        self._login('admin1', 'admin')
        resp = self.client.post('/eoc/alerts/issue', data={
            'title': 'Should not publish', 'message': 'x', 'severity': 'LOW',
        })
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Alert.query.filter_by(title='Should not publish').first())

    def test_admin_cannot_view_official_alerts_page(self):
        self._login('admin1', 'admin')
        resp = self.client.get('/eoc/alerts', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_retired_coordinator_cannot_view_official_alerts_page(self):
        self._login('coord1', 'agency_coordinator')
        resp = self.client.get('/eoc/alerts', follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    # -- Report (incident-level notes) ---------------------------------

    def test_eoc_can_log_incident_report(self):
        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/eoc/incidents/{self.incident_id}/log-report', data={
            'title': 'Initial triage', 'content': 'Confirmed via 3 citizen reports.', 'report_type': 'TRIAGE',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            report = Report.query.filter_by(incident_id=self.incident_id).first()
            self.assertIsNotNone(report)
            self.assertEqual(report.report_type, 'TRIAGE')

    def test_citizen_cannot_log_incident_report(self):
        self._login('cit1', 'citizen')
        resp = self.client.post(f'/eoc/incidents/{self.incident_id}/log-report', data={
            'title': 'hack', 'content': 'should not work',
        })
        self.assertEqual(resp.status_code, 302)
        with self.app.app_context():
            self.assertEqual(Report.query.count(), 0)

    def test_eoc_incident_detail_page_renders(self):
        self._login('eoc1', 'eoc_staff')
        resp = self.client.get(f'/eoc/incidents/{self.incident_id}')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
