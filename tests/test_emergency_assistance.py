import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_emergency_assistance.db'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB_PATH}')

from app import app, db
from models import User
from seed.demo_data import seed_geography_data
from services.hotlines import normalize_number

HOTLINE_ENV_VARS = ('HOTLINE_GENERAL', 'HOTLINE_MEDICAL', 'HOTLINE_POLICE', 'HOTLINE_FIRE', 'HOTLINE_CDRRMO')


def tel_links(html):
    return re.findall(r'href="tel:([^"]*)"', html)


class NormalizeNumberTestCase(unittest.TestCase):
    def test_keeps_display_and_strips_dial_string(self):
        self.assertEqual(normalize_number('(049) 502-1234'), ('(049) 502-1234', '0495021234'))

    def test_keeps_leading_plus_for_international_format(self):
        self.assertEqual(normalize_number('+63 917 123 4567'), ('+63 917 123 4567', '+639171234567'))

    def test_accepts_three_digit_emergency_numbers(self):
        self.assertEqual(normalize_number('911'), ('911', '911'))

    def test_rejects_unusable_values(self):
        for bad in (None, '', '   ', '12', 'call the office', '0917-XXX-XXXX', 'tel:911', '911;rm', '1' * 16,
                    '911" onclick="x'):
            self.assertIsNone(normalize_number(bad), bad)


class EmergencyAssistancePageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, PUBLIC_REPORTING_ENABLED=False)
        self.client = self.app.test_client()
        env = {name: '' for name in HOTLINE_ENV_VARS}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in HOTLINE_ENV_VARS:
            os.environ.pop(name, None)

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            seed_geography_data()
            db.session.add_all([
                User(username='ea_citizen', email='ea-citizen@example.com', password='secret',
                     role='citizen', email_verified=True),
                User(username='ea_eoc', email='ea-eoc@example.com', password='secret',
                     role='eoc_staff', email_verified=True),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def _login(self, username, role):
        with self.client.session_transaction() as session:
            session['username'] = username
            session['role'] = role

    # -- the page itself -------------------------------------------------

    def test_page_is_public_and_has_no_form(self):
        response = self.client.get('/emergency-assistance')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Need immediate help?', html)
        self.assertNotIn('<form', html)
        self.assertNotIn('type="file"', html)

    def test_defaults_to_national_number_and_never_invents_office_number(self):
        html = self.client.get('/emergency-assistance').get_data(as_text=True)
        links = tel_links(html)
        # hero + medical + police + fire + disaster (falls back) = 5 links, all 911
        self.assertEqual(links, ['911'] * 5)
        self.assertIn('This office line is not set up yet', html)

    def test_configured_numbers_are_used_per_service(self):
        env = {
            'HOTLINE_GENERAL': '911',
            'HOTLINE_MEDICAL': '(049) 111-2222',
            'HOTLINE_POLICE': '117',
            'HOTLINE_FIRE': '160',
            'HOTLINE_CDRRMO': '+63 49 502 0000',
        }
        with patch.dict(os.environ, env):
            html = self.client.get('/emergency-assistance').get_data(as_text=True)
        self.assertEqual(tel_links(html), ['911', '0491112222', '117', '160', '+63495020000'])
        self.assertIn('(049) 111-2222', html)
        self.assertIn('+63 49 502 0000', html)
        self.assertNotIn('This office line is not set up yet', html)

    def test_invalid_configured_number_falls_back_instead_of_rendering_it(self):
        with patch.dict(os.environ, {'HOTLINE_MEDICAL': 'ask at the office', 'HOTLINE_CDRRMO': '0917-XXX-XXXX'}):
            html = self.client.get('/emergency-assistance').get_data(as_text=True)
        self.assertNotIn('ask at the office', html)
        self.assertNotIn('XXX', html)
        self.assertEqual(tel_links(html), ['911'] * 5)

    def test_signed_out_visitor_gets_plain_layout_and_not_the_login_redirect(self):
        html = self.client.get('/emergency-assistance').get_data(as_text=True)
        self.assertNotIn('id="sidebarContainer"', html)
        self.assertIn("const publicPages = ['citizen.emergency_assistance']", html)
        self.assertIn('Sign in to see active alerts', html)

    def test_signed_in_citizen_gets_the_sidebar(self):
        self._login('ea_citizen', 'citizen')
        html = self.client.get('/emergency-assistance').get_data(as_text=True)
        self.assertIn('id="sidebarContainer"', html)
        self.assertNotIn('Sign in to see active alerts', html)

    def test_page_is_translated_to_tagalog(self):
        self.client.get('/language/tl')
        html = self.client.get('/emergency-assistance').get_data(as_text=True)
        self.assertIn('Kailangan ng agarang tulong?', html)
        self.assertIn('Tumawag na', html)

    def test_only_admin_and_eoc_see_the_missing_office_number_notice(self):
        self.assertNotIn('Admin notice', self.client.get('/emergency-assistance').get_data(as_text=True))
        self._login('ea_citizen', 'citizen')
        self.assertNotIn('Admin notice', self.client.get('/emergency-assistance').get_data(as_text=True))
        self._login('ea_eoc', 'eoc_staff')
        self.assertIn('Admin notice', self.client.get('/emergency-assistance').get_data(as_text=True))
        with patch.dict(os.environ, {'HOTLINE_CDRRMO': '(049) 502-0000'}):
            self.assertNotIn('Admin notice', self.client.get('/emergency-assistance').get_data(as_text=True))

    def test_login_page_links_to_the_hotline_page(self):
        html = self.client.get('/login').get_data(as_text=True)
        self.assertIn('href="/emergency-assistance"', html)

    # -- retired reporting -------------------------------------------------

    def test_report_form_redirects_to_hotline_page_by_default(self):
        self._login('ea_citizen', 'citizen')
        get_response = self.client.get('/citizen-report')
        self.assertEqual(get_response.status_code, 302)
        self.assertTrue(get_response.headers['Location'].endswith('/emergency-assistance'))

        post_response = self.client.post('/citizen-report', data={'hazard_type': 'flood'})
        self.assertEqual(post_response.status_code, 302)
        self.assertTrue(post_response.headers['Location'].endswith('/emergency-assistance'))

    def test_online_sos_is_gone_by_default_and_creates_no_incident(self):
        from models import Incident
        self._login('ea_citizen', 'citizen')
        response = self.client.post('/emergency-sos', json={'location': 'Test', 'latitude': 14.3, 'longitude': 120.9})
        self.assertEqual(response.status_code, 410)
        payload = response.get_json()
        self.assertFalse(payload['success'])
        self.assertTrue(payload['redirect'].endswith('/emergency-assistance'))
        with self.app.app_context():
            self.assertEqual(Incident.query.count(), 0)

    def test_legacy_reporting_can_be_switched_back_on(self):
        self._login('ea_citizen', 'citizen')
        self.app.config['PUBLIC_REPORTING_ENABLED'] = True
        response = self.client.get('/citizen-report')
        self.assertEqual(response.status_code, 200)

    # -- citizen UI --------------------------------------------------------

    def test_citizen_navigation_offers_calling_instead_of_reporting(self):
        self._login('ea_citizen', 'citizen')
        html = self.client.get('/citizen-dashboard').get_data(as_text=True)
        self.assertIn('href="/emergency-assistance"', html)
        self.assertNotIn('href="/citizen-report"', html)
        self.assertNotIn('triggerSOS()', html)
        self.assertNotIn('Report an Incident', html)
        self.assertNotIn('Your Recent Reports', html)
        self.assertIn('href="tel:911"', html)

    def test_citizen_navigation_restores_reporting_when_flag_is_on(self):
        self._login('ea_citizen', 'citizen')
        self.app.config['PUBLIC_REPORTING_ENABLED'] = True
        html = self.client.get('/citizen-dashboard').get_data(as_text=True)
        self.assertIn('href="/citizen-report"', html)
        self.assertIn('triggerSOS()', html)

    def test_resources_page_uses_configured_numbers_not_hardcoded_ones(self):
        self._login('ea_citizen', 'citizen')
        with patch.dict(os.environ, {'HOTLINE_FIRE': '160', 'HOTLINE_CDRRMO': '(049) 502-0000'}):
            html = self.client.get('/citizen-resources').get_data(as_text=True)
        self.assertIn('href="tel:160"', html)
        self.assertIn('href="tel:0495020000"', html)
        self.assertNotIn('819-9999', html)
        self.assertNotIn('427-8888', html)


if __name__ == '__main__':
    unittest.main()
