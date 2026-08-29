import os
import unittest

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
# See tests/test_responder_routes.py for why this must be set before `app` is imported.
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_operational_data_features.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from app import app, db
from models import (
    Alert,
    EvacuationCenter,
    Facility,
    Incident,
    Report,
    ResourceRequest,
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

            for uname, role, agency in [
                ('admin1', 'admin', None),
                ('eoc1', 'eoc_staff', None),
                ('coord1', 'agency_coordinator', 'BFP'),
                ('cmd1', 'incident_commander', None),
                ('cit1', 'citizen', None),
            ]:
                db.session.add(User(
                    username=uname, email=f'{uname}@example.com', password='secret',
                    role=role, agency=agency, email_verified=True,
                ))
            db.session.add(Incident(
                hazard_type='flood', location='Test Barangay', level='HIGH', score=70.0,
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

    def test_eoc_can_update_evacuation_center_occupancy(self):
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
            'occupancy': '85', 'status': 'OPEN',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            updated = EvacuationCenter.query.get(center_id)
            self.assertEqual(updated.occupancy, 85)

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

    # -- ResourceRequest --------------------------------------------------

    def test_coordinator_submit_and_eoc_approve_resource_request(self):
        self._login('coord1', 'agency_coordinator')
        resp = self.client.post('/coordinator/resource-requests/submit', data={
            'incident_id': str(self.incident_id), 'resource_type': 'Rescue Boats', 'quantity': '2',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            request_row = ResourceRequest.query.first()
            self.assertIsNotNone(request_row)
            self.assertEqual(request_row.status, 'OPEN')
            self.assertEqual(request_row.agency, 'BFP')
            request_id = request_row.id

        self._login('eoc1', 'eoc_staff')
        resp = self.client.post(f'/eoc/resource-requests/{request_id}/decide', data={
            'decision': 'APPROVED', 'notes': 'go ahead',
        })
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            updated = ResourceRequest.query.get(request_id)
            self.assertEqual(updated.status, 'APPROVED')
            self.assertEqual(updated.decision_notes, 'go ahead')

    def test_coordinator_cannot_decide_resource_request(self):
        with self.app.app_context():
            resource_request = ResourceRequest(
                incident_id=self.incident_id, resource_type='Ambulances', quantity=1,
                agency='BFP', status='OPEN',
            )
            db.session.add(resource_request)
            db.session.commit()
            request_id = resource_request.id

        self._login('coord1', 'agency_coordinator')
        resp = self.client.post(f'/eoc/resource-requests/{request_id}/decide', data={'decision': 'FULFILLED'})
        self.assertEqual(resp.status_code, 302)

        with self.app.app_context():
            unchanged = ResourceRequest.query.get(request_id)
            self.assertEqual(unchanged.status, 'OPEN')

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
            resolved = Alert.query.get(alert_id)
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

    def test_coordinator_can_still_view_official_alerts_page(self):
        """Regression guard: only Admin's access was removed. Commander and
        Coordinator keep their existing read access to official alerts
        (they can view but, per test_coordinator_cannot_issue_alert above,
        not issue/resolve them)."""
        self._login('coord1', 'agency_coordinator')
        resp = self.client.get('/eoc/alerts', follow_redirects=False)
        self.assertEqual(resp.status_code, 200)

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
