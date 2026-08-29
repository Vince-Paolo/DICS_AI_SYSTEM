import os
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-secret')
from app import app, db
from models import User

with app.app_context():
    db.create_all()
    user = User(username='eoc_test', email='eoc@test.com', password='pw', role='eoc', email_verified=True)
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'eoc_test'
        sess['role'] = 'eoc'

    response = client.get('/eoc/resource-requests')
    print('status', response.status_code)
    print(response.get_data(as_text=True)[:2000])
