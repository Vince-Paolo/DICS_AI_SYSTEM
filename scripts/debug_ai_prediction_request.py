import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app, db
from models import User

with app.app_context():
    db.drop_all()
    db.create_all()
    user = User(username='test_coordinator', email='test@example.com', password='pass', role='COORDINATOR')
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['username'] = 'test_coordinator'
        sess['role'] = 'COORDINATOR'
    response = client.post('/ai-prediction', data={
        'hazard_type': 'flood',
        'rainfall': '120',
        'river_level': '3.2',
        'humidity_pct': '85',
        'population_density': '2500',
    }, follow_redirects=True)
    print('STATUS', response.status_code)
    print('LOCATION', response.headers.get('Location'))
    print('BODY')
    print(response.data.decode('utf-8'))
