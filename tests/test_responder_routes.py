import unittest

from app import app, db
from models import User


class ResponderRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.client = self.app.test_client()

        with self.app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                username='responder1',
                email='responder@example.com',
                password='secret',
                role='field_responder',
                agency='BFP',
                email_verified=True,
            )
            db.session.add(user)
            db.session.commit()

    def test_field_responder_dashboard_requires_login(self):
        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 302)

    def test_field_responder_dashboard_renders_for_role(self):
        with self.client.session_transaction() as session:
            session['username'] = 'responder1'
            session['role'] = 'field_responder'
            session['agency'] = 'BFP'

        response = self.client.get('/responder-dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Field Responder', response.data)


if __name__ == '__main__':
    unittest.main()
