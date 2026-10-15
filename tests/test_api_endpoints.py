import os
import re
import unittest
import base64
from datetime import datetime, timezone
import zlib
from unittest.mock import patch
from sqlalchemy import event

os.environ.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY') or 'development-secret')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_api_endpoints.db'))
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

from app import app, db
from models import Barangay, CitizenReport, EvacuationCenter, Facility, Incident, IncidentResponse, Municipality, Province, Resource, Task, User
from seed.demo_data import seed_geography_data


def decompressed_pdf_content(pdf_data):
    streams = re.findall(rb'stream\r?\n(.*?)(?=\r?\n?endstream)', pdf_data, re.DOTALL)
    content = []
    for stream in streams:
        try:
            encoded_stream = stream.strip()
            if encoded_stream.startswith(b'<~'):
                decoded_stream = base64.a85decode(encoded_stream, adobe=True)
            else:
                if encoded_stream.endswith(b'~>'):
                    encoded_stream = encoded_stream[:-2]
                decoded_stream = base64.a85decode(encoded_stream)
            content.append(zlib.decompress(decoded_stream))
        except zlib.error:
            continue
        except ValueError:
            continue
    return b'\n'.join(content)


class ApiEndpointFunctionalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        # These tests cover the legacy citizen report form / SOS, which are
        # retired by default (see tests/test_emergency_assistance.py).
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, PUBLIC_REPORTING_ENABLED=True)
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            seed_geography_data()
            db.session.add_all([
                User(
                    username='api_citizen',
                    email='api-citizen@example.com',
                    password='secret',
                    role='citizen',
                    email_verified=True,
                ),
                User(
                    username='api_commander',
                    email='api-commander@example.com',
                    password='secret',
                    role='incident_commander',
                    email_verified=True,
                ),
                User(
                    username='api_coordinator',
                    email='api-coordinator@example.com',
                    password='secret',
                    role='imt_staff',
                    email_verified=True,
                ),
                User(
                    username='api_dispatcher',
                    email='api-dispatcher@example.com',
                    password='secret',
                    role='dispatcher',
                    email_verified=True,
                ),
            ])
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()

    def _login(self, username, role):
        with self.client.session_transaction() as session:
            session['username'] = username
            session['role'] = role

    def test_municipalities_api_returns_sorted_records_for_a_province(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            province_id = 1

        response = self.client.get(f'/api/municipalities/{province_id}')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('municipalities', payload)
        names = [item['name'] for item in payload['municipalities']]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(set(item) == {'id', 'name'} for item in payload['municipalities']))

    def test_hazard_map_province_dropdown_lists_real_provinces(self):
        """The province filter used to only ever show whatever provinces
        happened to be attached to currently-plotted incidents, so with few
        or no incidents on the map it rendered as good as empty. It should
        always list the real, seeded provinces regardless of what's
        currently plotted."""
        self._login('api_citizen', 'citizen')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        with self.app.app_context():
            province_names = [p.name for p in Province.query.all()]
        self.assertTrue(len(province_names) > 0)
        for name in province_names:
            self.assertIn(f'>{name}<', html)

    def test_hazard_map_search_bar_is_rendered_for_client_filtering(self):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn('id="languageSwitcher"', html)
        self.assertIn(
            'class="language-switcher d-flex align-items-center justify-content-between flex-wrap gap-3 mb-3"',
            html,
        )
        self.assertNotIn('language-switcher position-fixed', html)
        self.assertIn('id="hazardSearchInput"', html)
        self.assertIn('Search hazard areas...', html)

    def test_hazard_map_credits_open_meteo_rainfall_data(self):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer"',
            response.get_data(as_text=True),
        )

    def test_public_landing_page_links_to_login_and_emergency_assistance(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('public', response.headers['Cache-Control'])
        html = response.get_data(as_text=True)
        self.assertIn('One city. One coordinated response.', html)
        self.assertIn('href="/login"', html)
        self.assertIn('href="/emergency-assistance"', html)
        self.assertIn("const publicPages = ['landing', 'citizen.emergency_assistance']", html)
        self.assertNotIn('name="password"', html)

        login_page = self.client.get('/login')
        self.assertEqual(login_page.status_code, 200)
        self.assertIn('action="/login"', login_page.get_data(as_text=True))

        hotline_page = self.client.get('/emergency-assistance')
        self.assertEqual(hotline_page.status_code, 200)
        self.assertIn('public', hotline_page.headers['Cache-Control'])

    def test_signed_in_user_visiting_landing_page_still_sees_landing_page(self):
        self._login('api_dispatcher', 'dispatcher')

        response = self.client.get('/', follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('private', response.headers['Cache-Control'])
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('One city. One coordinated response.', html)
        self.assertIn("const authPages = ['login', 'register'", html)
        self.assertNotIn('id="global-control-number"', html)

    def test_landing_page_is_translated_to_filipino(self):
        self.client.get('/language/fil_PH?next=/')

        response = self.client.get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Isang lungsod. Iisang pinag-ugnay na pagtugon.', html)
        self.assertIn('Pangunahing pahina ng DICS AI', html)
        self.assertIn('Mag-login', html)
        self.assertIn('Maliwanag na tema', html)
        self.assertNotIn('Open the operations system', html)

    def test_shared_layout_uses_local_bootstrap_assets(self):
        response = self.client.get('/login')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('/static/vendor/bootstrap/css/bootstrap.min.css', html)
        self.assertIn('/static/vendor/bootstrap-icons/font/bootstrap-icons.css', html)
        self.assertIn('/static/vendor/bootstrap/js/bootstrap.bundle.min.js', html)
        self.assertNotIn('cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap', html)
        self.assertNotIn('cdn.jsdelivr.net/npm/bootstrap-icons', html)

        for asset in (
            'vendor/bootstrap/css/bootstrap.min.css',
            'vendor/bootstrap/js/bootstrap.bundle.min.js',
            'vendor/bootstrap-icons/font/bootstrap-icons.css',
            'vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2',
            'css/theme.css',
            'css/landing.css',
        ):
            with self.subTest(asset=asset):
                asset_response = self.client.get(f'/static/{asset}')
                self.assertEqual(asset_response.status_code, 200)

        worker = self.client.get('/service-worker.js').get_data(as_text=True)
        self.assertIn('/static/vendor/bootstrap/css/bootstrap.min.css', worker)
        self.assertIn('/static/css/sidebar.css', worker)
        self.assertIn('/static/css/theme.css', worker)
        self.assertIn('/static/css/tokens.css', worker)
        self.assertIn('/static/css/landing.css', worker)
        self.assertIn("cachePublicPage('/')", worker)
        self.assertIn("cachePublicPage('/emergency-assistance')", worker)
        self.assertIn("url.pathname === '/' || url.pathname === '/emergency-assistance'", worker)
        self.assertIn("cache.match(request, { ignoreSearch: true })", worker)

    def test_chartjs_is_local_on_dashboard_and_analytics(self):
        with self.app.app_context():
            db.session.add(User(
                username='api_dashboard_admin',
                email='api-dashboard-admin@example.com',
                password='secret',
                role='admin',
                email_verified=True,
            ))
            db.session.commit()

        self._login('api_dashboard_admin', 'admin')
        for path in ('/dashboard', '/analytics'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('/static/vendor/chartjs/chart.umd.js', html)
                self.assertIn('/static/js/chart-theme.js', html)
                self.assertIn('/static/css/theme.css', html)
                self.assertIn('/static/css/tokens.css', html)
                self.assertNotIn('cdn.jsdelivr.net/npm/chart.js', html)
                self.assertIn('role="img" aria-label=', html)

        chart_bundle = self.client.get('/static/vendor/chartjs/chart.umd.js')
        self.assertEqual(chart_bundle.status_code, 200)
        self.assertIn(b'Chart.js v4.4.7', chart_bundle.data[:300])

        worker = self.client.get('/service-worker.js').get_data(as_text=True)
        self.assertIn('/static/vendor/chartjs/chart.umd.js', worker)
        self.assertIn('/static/js/chart-theme.js', worker)
        self.assertIn(
            f"dics-app-shell-{self.app.config['STATIC_ASSET_VERSION']}",
            worker,
        )
        self.assertNotIn('__STATIC_ASSET_VERSION__', worker)

        tokens_css = self.client.get('/static/css/tokens.css').get_data(as_text=True)
        style_css = self.client.get('/static/css/style.css').get_data(as_text=True)
        for token in (
            '--table-head-bg:',
            '--table-head-text:',
            '--table-body-text:',
            '--table-row-alt:',
            '--table-row-hover:',
            '--chart-series-1:',
        ):
            with self.subTest(token=token):
                self.assertIn(token, tokens_css)
        self.assertIn('var(--table-head-bg)', style_css)
        self.assertIn('var(--table-row-hover)', style_css)

    def test_heat_map_and_navigation_are_available_without_tutorial(self):
        self._login('api_commander', 'incident_commander')

        response = self.client.get('/hazard-map')

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/hazard-map"', html)
        self.assertIn('id="heatMapToggle"', html)
        self.assertIn("document.getElementById('heatMapToggle').addEventListener('click'", html)
        self.assertIn('const heatLayer = L.heatLayer([],', html)
        self.assertNotIn('tutorial', html.lower())
        render_markers = html.split('function renderMarkers(', 1)[1].split('function getHeatWeight', 1)[0]
        self.assertIn('renderHeatLayer();', render_markers)

    def test_filipino_citizen_pages_and_flashes_use_fil_ph_catalog(self):
        self._login('api_citizen', 'citizen')
        self.client.get('/language/fil_PH?next=/citizen-alerts')
        with self.client.session_transaction() as session:
            session['_flashes'] = [('success', 'Registration successful! You can now log in.')]

        alerts_html = self.client.get('/citizen-alerts').get_data(as_text=True)
        self.assertIn('Mga Emergency Alert', alerts_html)
        self.assertIn('Matagumpay ang pagpaparehistro! Maaari ka nang mag-log in.', alerts_html)

        centers_html = self.client.get('/citizen-evacuation-centers').get_data(as_text=True)
        self.assertIn('Mga Sentro ng Paglikas', centers_html)

    @patch('app.get_typhoon_tracks', return_value=[{'track': [[14.0, 121.0]]}])
    @patch('app.get_rainfall_watch', return_value=[{'lat': 14.0, 'lon': 121.0}])
    def test_hazard_map_rainfall_and_typhoon_tracking_apis_return_data(
        self, mock_rainfall, mock_typhoon,
    ):
        self._login('api_citizen', 'citizen')

        rainfall_response = self.client.get('/api/hazard-layers/rainfall')
        typhoon_response = self.client.get('/api/hazard-layers/typhoon-tracks')

        self.assertEqual(rainfall_response.status_code, 200)
        self.assertEqual(typhoon_response.status_code, 200)

        rainfall = rainfall_response.get_json()
        typhoon = typhoon_response.get_json()

        self.assertIsInstance(rainfall, list)
        self.assertIsInstance(typhoon, list)
        self.assertIn('lat', rainfall[0])
        self.assertIn('lon', rainfall[0])
        self.assertIn('track', typhoon[0])
        mock_rainfall.assert_called_once_with()
        mock_typhoon.assert_called_once_with()

    def test_barangays_api_returns_sorted_records_for_a_municipality(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            municipality_id = 1

        response = self.client.get(f'/api/barangays/{municipality_id}')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('barangays', payload)
        names = [item['name'] for item in payload['barangays']]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(set(item) == {'id', 'name'} for item in payload['barangays']))

    @patch('app.get_earthquake_data', return_value=[{'magnitude': 4.2, 'place': 'Test'}])
    @patch('app.get_all_weather_data', return_value={'Cavite': {'rainfall': 12}})
    def test_realtime_data_api_returns_weather_and_earthquake_payloads(self, mock_weather, mock_earthquakes):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/api/realtime-data')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'weather': {'Cavite': {'rainfall': 12}},
            'earthquakes': [{'magnitude': 4.2, 'place': 'Test'}],
        })
        mock_weather.assert_called_once_with()
        mock_earthquakes.assert_called_once_with()

    @patch('app.get_earthquake_data', return_value=[{'magnitude': 5.1}])
    def test_dashboard_stats_api_reports_only_the_logged_in_users_incidents(self, mock_earthquakes):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            db.session.add(Incident(
                user_id=user.id,
                hazard_type='flood',
                message='Test incident',
                score=62.5,
                alert=True,
                status='ACTIVE',
            ))
            db.session.add(Incident(
                user_id=User.query.filter_by(username='api_commander').one().id,
                hazard_type='fire',
                message='Other incident',
                score=99,
                alert=True,
                status='ACTIVE',
            ))
            db.session.commit()

        response = self.client.get('/api/dashboard-stats')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'alert_count': 1,
            'total_incidents': 1,
            'latest_risk_score': 62.5,
            'latest_earthquake_magnitude': 5.1,
        })
        mock_earthquakes.assert_called_once_with()

    def test_map_pins_api_returns_only_active_geolocated_incidents(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            report = user.citizen_reports[0] if user.citizen_reports else None
            if report is None:
                from models import CitizenReport
                report = CitizenReport(
                    user_id=user.id,
                    hazard_type='flood',
                    severity='HIGH',
                    location='Cavite',
                    description='Mapped flooding',
                    gps_latitude=14.28,
                    gps_longitude=120.88,
                )
                report.province_id = Province.query.first().id
                report.municipality_id = Municipality.query.filter_by(province_id=report.province_id).first().id
                db.session.add(report)
                db.session.flush()
            db.session.add(Incident(
                user_id=user.id,
                citizen_report_id=report.id,
                hazard_type='flood',
                location='Cavite',
                message='Mapped flooding',
                level='Severe',
                alert=False,
                status='ACTIVE',
                reported_by='citizen',
            ))
            db.session.add(Incident(
                user_id=user.id,
                hazard_type='fire',
                location='No coordinates',
                message='Not mapped',
                alert=True,
                status='ACTIVE',
            ))
            commander = User.query.filter_by(username='api_commander').one()
            db.session.add(Incident(
                user_id=commander.id,
                hazard_type='storm',
                location='Another household',
                message='Private report from another user',
                level='High',
                alert=True,
                status='ACTIVE',
                reported_by='citizen',
                latitude=14.4,
                longitude=120.95,
            ))
            db.session.commit()

        response = self.client.get('/api/map-pins')

        self.assertEqual(response.status_code, 200)
        pins = response.get_json()
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]['hazard_type'], 'flood')
        self.assertEqual(pins[0]['lat'], 14.28)
        self.assertEqual(pins[0]['lng'], 120.88)
        self.assertEqual(pins[0]['level'], 'Severe')
        self.assertIsNotNone(pins[0]['province'])
        self.assertIsNotNone(pins[0]['municipality'])
        self.assertEqual(pins[0]['status'], 'ACTIVE')
        self.assertEqual(pins[0]['reported_by'], 'citizen')

        self._login('api_commander', 'incident_commander')
        staff_pins = self.client.get('/api/map-pins').get_json()
        self.assertEqual(len(staff_pins), 2)

    def test_emergency_sos_incident_appears_on_hazard_map_with_coordinates(self):
        self._login('api_citizen', 'citizen')

        response = self.client.post('/emergency-sos', json={
            'location': 'Barangay Uno',
            'latitude': 14.32,
            'longitude': 120.9,
        })

        self.assertEqual(response.status_code, 200)
        pins = self.client.get('/api/map-pins').get_json()
        self.assertTrue(any(
            pin.get('hazard_type') == 'EMERGENCY' and pin.get('lat') == 14.32 and pin.get('lng') == 120.9
            for pin in pins
        ))

    def test_citizen_cannot_access_operational_map_resources(self):
        self._login('api_citizen', 'citizen')

        response = self.client.get('/api/map-resources')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'error': 'Forbidden'})

    def test_citizen_cannot_trigger_live_prediction(self):
        self._login('api_citizen', 'citizen')

        with patch('app.get_weather_data') as get_weather, patch('app.predict_hazard') as predict_hazard:
            response = self.client.get('/live-prediction?city=Lipa')

        self.assertEqual(response.status_code, 403)
        get_weather.assert_not_called()
        predict_hazard.assert_not_called()

    def test_login_and_logout_responses_disable_browser_cache(self):
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

        with self.client.session_transaction() as session:
            session['username'] = 'api_citizen'
            session['role'] = 'citizen'

        response = self.client.get('/login')
        self.assertEqual(response.status_code, 302)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

        response = self.client.get('/logout', follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('no-store', response.headers.get('Cache-Control', '').lower())

    def test_file_storage_raises_when_s3_region_is_placeholder(self):
        from services.file_storage import FileStorage

        app_stub = type('AppStub', (), {
            'config': {
                'FILE_STORAGE_BACKEND': 's3',
                'FILE_STORAGE_BUCKET': 'demo-bucket',
                'FILE_STORAGE_PREFIX': 'uploads',
                'FILE_STORAGE_REGION': '...',
                'FILE_STORAGE_ENDPOINT_URL': 'https://s3.example.com',
                'FILE_STORAGE_ACCESS_KEY_ID': 'demo-key',
                'FILE_STORAGE_SECRET_ACCESS_KEY': 'demo-secret',
                'UPLOAD_FOLDER': os.path.join('instance', 'uploads', 'citizen_reports'),
            }
        })()

        with self.assertRaises(RuntimeError):
            FileStorage(app_stub)

    def test_map_operational_layer_endpoints_return_geolocated_centers_and_resources(self):
        self._login('api_coordinator', 'imt_staff')
        with self.app.app_context():
            user = User.query.filter_by(username='api_coordinator').one()
            report = CitizenReport(
                user_id=user.id,
                hazard_type='flood',
                severity='HIGH',
                location='Cavite',
                description='Response location',
                gps_latitude=14.28,
                gps_longitude=120.88,
            )
            db.session.add(report)
            db.session.flush()
            incident = Incident(
                user_id=user.id,
                citizen_report_id=report.id,
                hazard_type='flood',
                location='Cavite',
                message='Response location',
                level='High',
                status='ACTIVE',
            )
            db.session.add(incident)
            db.session.flush()
            response = IncidentResponse(incident_id=incident.id, commander_id=user.id, status='ACTIVE')
            facility = Facility(name='Mapped Shelter', facility_type='Evacuation Center', latitude=14.3, longitude=120.9)
            db.session.add_all([response, facility])
            db.session.flush()
            db.session.add(EvacuationCenter(facility_id=facility.id, capacity=100, occupancy=20, status='OPEN'))
            db.session.add(Resource(incident_response_id=response.id, resource_type='Water', quantity=5, status='DEPLOYED'))
            db.session.commit()

        centers = self.client.get('/api/map-evacuation-centers')
        resources = self.client.get('/api/map-resources')

        self.assertEqual(centers.status_code, 200)
        self.assertEqual(resources.status_code, 200)
        self.assertEqual(centers.get_json()[0]['name'], 'Mapped Shelter')
        self.assertEqual(resources.get_json()[0]['resource_type'], 'Water')

    def test_analytics_api_returns_operational_summary_for_imt(self):
        self._login('api_coordinator', 'imt_staff')

        response = self.client.get('/api/analytics-data')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['incident_counts'], {})
        self.assertEqual(payload['response_time']['total_resolved'], 0)
        self.assertEqual(payload['response_time']['average_minutes'], 0)
        self.assertIn('resource_utilization', payload)
        self.assertEqual(payload['resource_utilization']['status_counts'], {})
        self.assertEqual(payload['resource_utilization']['type_counts'], {})

    def test_citizen_dashboard_labels_alert_count_as_system_wide(self):
        self._login('api_citizen', 'citizen')
        with self.app.app_context():
            user = User.query.filter_by(username='api_citizen').one()
            db.session.add(Incident(
                user_id=user.id,
                hazard_type='flood',
                location='Test location',
                message='Alert for dashboard scope test',
                level='High',
                status='ACTIVE',
                alert=True,
            ))
            db.session.commit()

        response = self.client.get('/citizen-dashboard')

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'One active alert is recorded system-wide.', response.data)
        self.assertIn(b'not a location-specific risk assessment', response.data)
        self.assertNotIn(b'active alerts near you', response.data)
        self.assertNotIn(b'No active alerts in your area', response.data)

    def test_analytics_api_includes_monthly_and_geography_breakdowns(self):
        self._login('api_coordinator', 'imt_staff')
        with self.app.app_context():
            municipality = Municipality.query.first()
            barangay = Barangay.query.filter_by(municipality_id=municipality.id).first()
            municipality_name = municipality.name
            barangay_name = barangay.name
            other_barangay = Barangay.query.filter(Barangay.id != barangay.id).first()
            db.session.add_all([
                Incident(
                    hazard_type='flood',
                    location='Flooded barangay',
                    message='Flooding in same barangay',
                    level='Severe',
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    status='ACTIVE',
                    created_at=datetime(2026, 2, 5),
                ),
                Incident(
                    hazard_type='flood',
                    location='Flooded barangay again',
                    message='Flooding repeated',
                    level='High',
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    status='VERIFIED',
                    created_at=datetime(2026, 2, 17),
                ),
                Incident(
                    hazard_type='fire',
                    location='Fire elsewhere',
                    message='Warehouse fire',
                    level='High',
                    municipality_id=municipality.id,
                    barangay_id=other_barangay.id,
                    status='ACTIVE',
                    created_at=datetime(2026, 2, 18),
                ),
            ])
            db.session.commit()

        response = self.client.get('/api/analytics-data?year=2026&month=2')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreaterEqual(len(payload['monthly_counts']), 1)
        self.assertTrue(any(item['month'] == '2026-02' and item['count'] >= 2 for item in payload['monthly_counts']))
        self.assertTrue(any(
            item['hazard_type'] == 'flood' and item['municipality'] == municipality_name and item['barangay'] == barangay_name and item['count'] == 2
            for item in payload['geo_counts']
        ))

        export_response = self.client.get('/api/analytics-export?format=csv&year=2026&month=2')
        self.assertEqual(export_response.status_code, 200)
        self.assertIn('hazard_type', export_response.get_data(as_text=True))

    def test_analytics_api_summarizes_year_status_patients_and_control_search(self):
        self._login('api_coordinator', 'imt_staff')
        with self.app.app_context():
            municipality = Municipality.query.first()
            barangay = Barangay.query.filter_by(municipality_id=municipality.id).first()
            incidents = [
                Incident(
                    hazard_type='EMERGENCY',
                    location='Old report',
                    message='Prior-year report',
                    status='CLOSED',
                    patient_count=1,
                    created_at=datetime(2024, 12, 31, 16, 0),
                ),
                Incident(
                    hazard_type='EMERGENCY',
                    location='San Pablo',
                    message='Flood response',
                    status='ACTIVE',
                    patient_count=2,
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    created_at=datetime(2026, 2, 5),
                ),
                Incident(
                    hazard_type='EMERGENCY',
                    location='San Pablo',
                    message='Fire response',
                    status='CLOSED',
                    patient_count=3,
                    municipality_id=municipality.id,
                    barangay_id=barangay.id,
                    created_at=datetime(2026, 2, 17),
                ),
                Incident(
                    hazard_type='EMERGENCY',
                    location='San Pablo',
                    message='March response',
                    status='ACTIVE',
                    patient_count=10,
                    created_at=datetime(2026, 3, 1),
                ),
            ]
            db.session.add_all(incidents)
            db.session.commit()
            target_control_number = incidents[1].control_number
            prior_year_control_number = incidents[0].control_number

        response = self.client.get('/api/analytics-data?year=2026&month=2')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['status_counts'], {'ACTIVE': 1, 'CLOSED': 1})
        self.assertEqual(payload['patient_counts']['total_patients'], 5)
        self.assertEqual(payload['patient_counts']['emergencies_with_patients'], 2)
        self.assertEqual(payload['patient_counts']['average_per_emergency'], 2.5)
        self.assertEqual(
            [record['patient_count'] for record in payload['patient_counts']['records']],
            [3, 2],
        )
        self.assertEqual(
            {item['year']: item['count'] for item in payload['year_counts']},
            {2025: 1, 2026: 3},
        )

        searched = self.client.get(
            '/api/analytics-data?year=2026&month=2'
            f'&control_number={target_control_number}'
        ).get_json()
        self.assertEqual(searched['control_number_query'], target_control_number)
        self.assertEqual(searched['patient_counts']['matching_emergencies'], 1)
        self.assertEqual(
            [record['control_number'] for record in searched['patient_counts']['records']],
            [target_control_number],
        )
        prior_year_search = self.client.get(
            '/api/analytics-data?year=2026&month=2'
            f'&control_number={prior_year_control_number}'
        ).get_json()
        self.assertEqual(prior_year_search['patient_counts']['matching_emergencies'], 1)
        self.assertEqual(
            prior_year_search['patient_counts']['records'][0]['control_number'],
            prior_year_control_number,
        )
        for wildcard_query in ('202_-00001', '%'):
            wildcard_search = self.client.get(
                '/api/analytics-data?year=2026&month=2'
                f'&control_number={wildcard_query}'
            ).get_json()
            self.assertEqual(
                wildcard_search['patient_counts']['matching_emergencies'],
                0,
                msg=f'{wildcard_query!r} should be treated as literal text',
            )

    def test_analytics_monthly_counts_use_manila_calendar_boundaries(self):
        self._login('api_coordinator', 'imt_staff')
        with self.app.app_context():
            db.session.add_all([
                Incident(
                    hazard_type='september-boundary', location='Manila', message='Just after local midnight',
                    level='High', status='ACTIVE', created_at=datetime(2026, 8, 31, 16, 0),
                ),
                Incident(
                    hazard_type='august-boundary', location='Manila', message='Just before local midnight',
                    level='High', status='ACTIVE', created_at=datetime(2026, 8, 31, 15, 59),
                ),
            ])
            db.session.commit()

        september = self.client.get('/api/analytics-data?year=2026&month=9').get_json()
        august = self.client.get('/api/analytics-data?year=2026&month=8').get_json()

        self.assertEqual(september['incident_counts'], {'september-boundary': 1})
        self.assertEqual(august['incident_counts'], {'august-boundary': 1})
        self.assertEqual(
            next(item['count'] for item in september['monthly_counts'] if item['month'] == '2026-09'),
            1,
        )

    def test_analytics_year_selector_uses_manila_year_at_utc_rollover(self):
        self._login('api_coordinator', 'imt_staff')
        with patch('app.datetime') as datetime_mock:
            datetime_mock.now.return_value = datetime(2025, 12, 31, 16, 30, tzinfo=timezone.utc)
            response = self.client.get('/analytics')

        self.assertEqual(response.status_code, 200)
        self.assertIn('const currentYear = 2026;', response.get_data(as_text=True))

    def test_analytics_pdf_paginates_and_queries_bounded_rows(self):
        self._login('api_coordinator', 'imt_staff')
        with self.app.app_context():
            barangays = Barangay.query.limit(100).all()
            db.session.add_all([
                Incident(
                    hazard_type='flood',
                    location=f'Location {index}',
                    message='Flooding reported',
                    level='High',
                    status='ACTIVE',
                    municipality_id=barangay.municipality_id,
                    barangay_id=barangay.id,
                    created_at=datetime(2026, 4, 15, 12),
                )
                for index, barangay in enumerate(barangays)
            ])
            db.session.commit()

        query_count = 0

        def count_query(*args, **kwargs):
            nonlocal query_count
            query_count += 1

        warmup_response = self.client.get('/api/analytics-data?year=2026&month=4')
        self.assertEqual(warmup_response.status_code, 200)
        with self.app.app_context():
            event.listen(db.engine, 'before_cursor_execute', count_query)
        try:
            analytics_response = self.client.get('/api/analytics-data?year=2026&month=4')
        finally:
            with self.app.app_context():
                event.remove(db.engine, 'before_cursor_execute', count_query)

        self.assertEqual(analytics_response.status_code, 200)
        self.assertLess(query_count, 25)

        pdf_response = self.client.get('/api/analytics-export?format=pdf&year=2026&month=4')
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, 'application/pdf')
        self.assertGreaterEqual(len(re.findall(rb'/Type\s*/Page\b', pdf_response.data)), 2)
        pdf_content = decompressed_pdf_content(pdf_response.data)
        self.assertIn(b'Monthly consolidation for 2026', pdf_content)
        self.assertIn(b'2026-04', pdf_content)

    def test_manila_time_filter_converts_utc_and_labels_local_time(self):
        formatter = self.app.jinja_env.filters['manila_time']

        rendered = formatter(datetime(2026, 8, 31, 19, 0), '%b %d, %Y %I:%M %p')

        self.assertEqual(rendered, 'Sep 01, 2026 03:00 AM PHT')

    def test_incident_response_stats_api_returns_commander_counts(self):
        self._login('api_commander', 'incident_commander')
        with self.app.app_context():
            commander = User.query.filter_by(username='api_commander').one()
            incident = Incident(hazard_type='flood', message='Response test', status='ACTIVE')
            db.session.add(incident)
            db.session.flush()
            response = IncidentResponse(
                incident_id=incident.id,
                commander_id=commander.id,
                status='ACTIVE',
            )
            db.session.add(response)
            db.session.flush()
            db.session.add(Task(
                incident_response_id=response.id,
                assigned_to_user_id=commander.id,
                assigned_by_id=commander.id,
                status='PENDING',
                title='Task',
                description='Test task',
            ))
            db.session.add(Resource(
                incident_response_id=response.id,
                status='DEPLOYED',
                quantity=3,
                resource_type='Water',
            ))
            db.session.commit()

        result = self.client.get('/api/incident-response-stats')

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), {
            'active_responses': 1,
            'pending_tasks': 1,
            'deployed_resources': 1,
        })


if __name__ == '__main__':
    unittest.main()
