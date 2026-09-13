from app import app
from flask import render_template

with app.test_request_context('/login'):
    login_html = render_template('pages/login.html', error=None)
    print('login_has_badge', 'AI provider:' in login_html)
with app.test_request_context('/ai-prediction'):
    ai_html = render_template('pages/ai_prediction.html',
        total_active_alerts=0,
        total_incidents=0,
        latest_risk_score=0,
        latest_earthquake_magnitude=0,
        prediction_locations=[],
        prediction=None,
        csrf_token=lambda: 'test-token',
    )
    print('ai_prediction_has_badge', 'AI provider:' in ai_html)
with app.test_request_context('/analytics'):
    analytics_html = render_template('pages/analytics.html',
        total_incidents=0,
        avg_score=0,
        active_responses=0,
        active_alerts=0,
        hazard_labels=[],
        hazard_counts=[],
        post_incident_reports=[],
        average_rating=0,
    )
    print('analytics_has_badge', 'AI provider:' in analytics_html)
