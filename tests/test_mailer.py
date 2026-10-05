import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('ADMIN_PASSWORD', 'Test-Admin-Pass1!')

from flask import Flask

from services import mailer


def make_app(**cfg):
    app = Flask(__name__)
    app.config.update({'RESEND_SUPPRESS_SEND': False, **cfg})
    return app


class MailerTestCase(unittest.TestCase):
    def test_auto_backend_prefers_smtp_when_host_set(self):
        with make_app(EMAIL_BACKEND='auto', SMTP_HOST='smtp.example.com').app_context():
            self.assertEqual(mailer.selected_backend(), 'smtp')
        with make_app(EMAIL_BACKEND='auto', SMTP_HOST='').app_context():
            self.assertEqual(mailer.selected_backend(), 'resend')

    def test_starttls_on_587_with_login(self):
        app = make_app(SMTP_HOST='smtp.example.com', SMTP_PORT=587, SMTP_USERNAME='u@example.com',
                       SMTP_PASSWORD='pw', SMTP_FROM_EMAIL='u@example.com')
        with app.app_context(), patch('services.mailer.smtplib.SMTP') as smtp:
            server = smtp.return_value
            server.__enter__.return_value = server
            self.assertTrue(mailer.send_email('to@example.com', 'Hi', 'Body'))
            server.starttls.assert_called_once()
            server.login.assert_called_once_with('u@example.com', 'pw')
            msg = server.send_message.call_args[0][0]
            self.assertEqual(msg['To'], 'to@example.com')
            self.assertEqual(msg['From'], 'u@example.com')

    def test_gmail_app_password_display_spaces_are_removed(self):
        app = make_app(SMTP_HOST='smtp.gmail.com', SMTP_PORT=587, SMTP_USERNAME='u@gmail.com',
                       SMTP_PASSWORD='abcd efgh ijkl mnop', SMTP_FROM_EMAIL='u@gmail.com')
        with app.app_context(), patch('services.mailer.smtplib.SMTP') as smtp:
            server = smtp.return_value
            server.__enter__.return_value = server
            self.assertTrue(mailer.send_email('to@example.com', 'Hi', 'Body'))
            server.login.assert_called_once_with('u@gmail.com', 'abcdefghijklmnop')

    def test_ssl_on_465(self):
        app = make_app(SMTP_HOST='smtp.example.com', SMTP_PORT=465, SMTP_USERNAME='u', SMTP_PASSWORD='p',
                       SMTP_FROM_EMAIL='u@example.com')
        with app.app_context(), patch('services.mailer.smtplib.SMTP_SSL') as smtp:
            server = smtp.return_value
            server.__enter__.return_value = server
            self.assertTrue(mailer.send_email('to@example.com', 'Hi', 'Body'))
            server.starttls.assert_not_called()

    def test_failure_returns_false(self):
        app = make_app(SMTP_HOST='smtp.example.com', SMTP_FROM_EMAIL='a@example.com')
        with app.app_context(), patch('services.mailer.smtplib.SMTP', side_effect=OSError('boom')):
            self.assertFalse(mailer.send_email('to@example.com', 'Hi', 'Body'))

    def test_suppress_skips_sending(self):
        app = make_app(SMTP_HOST='smtp.example.com', RESEND_SUPPRESS_SEND=True)
        with app.app_context(), patch('services.mailer.smtplib.SMTP') as smtp:
            self.assertTrue(mailer.send_email('to@example.com', 'Hi', 'Body'))
            smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
