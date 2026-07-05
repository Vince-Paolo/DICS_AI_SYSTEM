from flask import Blueprint, redirect, render_template, request, session, url_for

from models import db, User, Incident
from services.realtime_data import get_earthquake_data
from ai.prediction import predict_hazard

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/ai-prediction', methods=['GET', 'POST'])
def ai_prediction():
    if 'username' not in session:
        return redirect(url_for('login'))

    prediction = None
    if request.method == 'POST':
        hazard_type = request.form.get('hazard_type')
        rainfall = float(request.form.get('rainfall') or 0)
        river_level = float(request.form.get('river_level') or 0)
        soil_moisture = float(request.form.get('soil_moisture') or 0)
        population_density = float(request.form.get('population_density') or 0)

        prediction = predict_hazard(
            hazard_type=hazard_type,
            rainfall_mm=rainfall,
            river_level_m=river_level,
            soil_moisture_pct=soil_moisture,
            population_density=population_density,
        )

        user = User.query.filter_by(username=session['username']).first()
        if user:
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                rainfall_mm=rainfall,
                river_level_m=river_level,
                soil_moisture_pct=soil_moisture,
                population_density=population_density,
                score=prediction.get('score'),
                level=prediction.get('level'),
                message=prediction.get('message', 'Manual incident report created.'),
                alert=prediction.get('alert', False),
                status='REVIEWED' if prediction else 'NEW',
                reported_by='ai_prediction',
            )
            db.session.add(incident)
            db.session.commit()

    total_active_alerts = Incident.query.filter_by(alert=True).count()
    total_incidents = Incident.query.count()
    latest_incident = Incident.query.order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0

    earthquake_data = get_earthquake_data()
    latest_earthquake_magnitude = 0
    if earthquake_data and len(earthquake_data) > 0:
        latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

    return render_template('pages/ai_prediction.html',
                         prediction=prediction,
                         total_active_alerts=total_active_alerts,
                         total_incidents=total_incidents,
                         latest_risk_score=latest_risk_score,
                         latest_earthquake_magnitude=latest_earthquake_magnitude)
