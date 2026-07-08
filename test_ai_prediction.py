#!/usr/bin/env python3
"""Test script to debug the ai_prediction route."""

import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from app import app, db
from models import Incident, User
from services.realtime_data import get_earthquake_data
from flask import render_template_string

with app.app_context():
    try:
        # Test database queries
        total_active_alerts = Incident.query.filter_by(alert=True).count()
        total_incidents = Incident.query.count()
        latest_incident = Incident.query.order_by(Incident.created_at.desc()).first()
        latest_risk_score = float(latest_incident.score) if latest_incident and latest_incident.score is not None else 0.0

        # Test earthquake data
        earthquake_data = get_earthquake_data()
        latest_earthquake_magnitude = 0
        if earthquake_data and len(earthquake_data) > 0:
            latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

        print("✓ All data retrieved successfully")
        print(f"  - Total active alerts: {total_active_alerts}")
        print(f"  - Total incidents: {total_incidents}")
        print(f"  - Latest risk score: {latest_risk_score} (type: {type(latest_risk_score).__name__})")
        print(f"  - Latest earthquake magnitude: {latest_earthquake_magnitude}")

        # Try to render the template with the data
        template_test = """
        Risk Score: {{ "%.0f"|format(latest_risk_score) }}%
        Magnitude: {{ "%.1f"|format(latest_earthquake_magnitude) }}
        """
        
        result = render_template_string(template_test,
                                       latest_risk_score=latest_risk_score,
                                       latest_earthquake_magnitude=latest_earthquake_magnitude)
        print("\n✓ Template rendering successful")
        print(f"  {result}")
        
        print("\n✓ All tests passed!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
