import os
import re
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('ADMIN_PASSWORD', 'Test-Admin-Pass1!')
TEST_DB_PATH = os.path.abspath(os.path.join('instance', 'test_email_otp.db'))
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB_PATH}'

import app as app_module
from app import app, db, _utcnow_naive
from models import User

FORM = {
    'full_name': 'Juan Dela Cruz',
    'email': 'juan@example.com',
    'username': 'juan',
    'contact_number': '09171234567',
    'password': 'StrongPass1!',
}


class EmailOtpTestCase(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        previous_limiter_state = app_module.limiter.enabled
        app_module.limiter.enabled = False
        self.addCleanup(setattr, app_module.limiter, 'enabled', previous_limiter_state)
        self.client = app.test_client()
        self.sent = []
        patcher = patch.object(app_module, 'send_email', side_effect=self._fake_send)
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            db.drop_all()
            db.create_all()

    def _fake_send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True

    def last_code(self):
        return re.search(r'code is: (\d{6})', self.sent[-1][2]).group(1)

    def register(self, **overrides):
        return self.client.post('/register', data={**FORM, **overrides})

    def user(self, email='juan@example.com'):
        return User.query.filter_by(email=email).first()

    def test_register_creates_unverified_user_and_emails_code(self):
        resp = self.register()
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/verify-email'))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], 'juan@example.com')
        with app.app_context():
            u = self.user()
            self.assertFalse(u.email_verified)
            self.assertIsNotNone(u.verification_token)
            # the plaintext code must never be stored
            self.assertNotIn(self.last_code(), u.verification_token)

    def test_verify_page_renders(self):
        self.register()
        resp = self.client.get('/verify-email')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'juan@example.com', resp.data)
        self.assertIn(b"authPages = ['login', 'register', 'verify_email',", resp.data)

    def test_login_blocked_until_verified_then_allowed(self):
        self.register()
        resp = self.client.post('/login', data={'username': 'juan', 'password': FORM['password']})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/verify-email'))
        with self.client.session_transaction() as sess:
            self.assertNotIn('username', sess)

        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'code': self.last_code(), 'action': 'verify'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(resp.headers['Location'], ('/', '/login'))  # url_for('login') resolves to '/'
        with app.app_context():
            u = self.user()
            self.assertTrue(u.email_verified)
            self.assertIsNone(u.verification_token)

        resp = self.client.post('/login', data={'username': 'juan', 'password': FORM['password']})
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('username'), 'juan')

    def test_wrong_code_counts_attempts_then_locks(self):
        self.register()
        good = self.last_code()
        bad = '000000' if good != '000000' else '111111'
        for _ in range(5):
            resp = self.client.post('/verify-email', data={'email': FORM['email'], 'code': bad, 'action': 'verify'})
            self.assertEqual(resp.status_code, 200)
        # even the correct code is refused once attempts are exhausted
        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'code': good, 'action': 'verify'})
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            self.assertFalse(self.user().email_verified)

    def test_expired_code_rejected(self):
        self.register()
        with app.app_context():
            u = self.user()
            u.verification_expires_at = _utcnow_naive() - timedelta(minutes=1)
            db.session.commit()
        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'code': self.last_code(), 'action': 'verify'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'expired', resp.data)
        with app.app_context():
            self.assertFalse(self.user().email_verified)

    def test_resend_has_cooldown_then_issues_new_code(self):
        self.register()
        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'action': 'resend'})
        self.assertEqual(resp.status_code, 200)  # still inside cooldown
        self.assertEqual(len(self.sent), 1)

        with app.app_context():
            u = self.user()
            u.verification_sent_at = _utcnow_naive() - timedelta(seconds=120)
            db.session.commit()
        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'action': 'resend'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(self.sent), 2)

        resp = self.client.post('/verify-email', data={'email': FORM['email'], 'code': self.last_code(), 'action': 'verify'})
        self.assertIn(resp.headers['Location'], ('/', '/login'))  # url_for('login') resolves to '/'

    def test_resend_for_unknown_email_sends_nothing(self):
        self.client.post('/verify-email', data={'email': 'nobody@example.com', 'action': 'resend'})
        self.assertEqual(self.sent, [])

    def test_unverified_signup_can_be_retaken(self):
        self.register()
        self.register(password='AnotherPass2!')
        self.assertEqual(len(self.sent), 2)
        with app.app_context():
            self.assertEqual(User.query.filter_by(email='juan@example.com').count(), 1)

    def test_verified_email_or_username_still_rejected(self):
        self.register()
        self.client.post('/verify-email', data={'email': FORM['email'], 'code': self.last_code(), 'action': 'verify'})
        resp = self.register()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'already', resp.data)

    def test_legacy_user_without_token_can_still_login(self):
        from werkzeug.security import generate_password_hash
        with app.app_context():
            db.session.add(User(username='old', email='old@example.com', role='citizen',
                                password=generate_password_hash('LegacyPass1!'), email_verified=False))
            db.session.commit()
        resp = self.client.post('/login', data={'username': 'old', 'password': 'LegacyPass1!'})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp.headers['Location'].endswith('/verify-email'))

    def test_account_still_created_if_email_fails(self):
        with patch.object(app_module, 'send_email', return_value=False):
            resp = self.register()
        self.assertTrue(resp.headers['Location'].endswith('/verify-email'))
        with app.app_context():
            self.assertIsNotNone(self.user())


if __name__ == '__main__':
    unittest.main()
