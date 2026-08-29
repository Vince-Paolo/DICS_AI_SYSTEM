import json

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import db, User, Incident, AIRecommendation, AuditEvent
from services.realtime_data import get_earthquake_data
from ai.decision_support import predict_hazard
from services import permissions as permission_service

ai_bp = Blueprint('ai', __name__)


@ai_bp.route('/ai-prediction', methods=['GET', 'POST'])
def ai_prediction():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Restrict to operational roles only: Incident Commanders, EOC Staff, and Coordinators
    if not permission_service.has_any_role('COMMANDER', 'EOC', 'COORDINATOR', 'ADMIN'):
        flash('You do not have permission to run hazard predictions. Only incident commanders, EOC staff, and coordinators can perform this action.', 'danger')
        return redirect(url_for('dashboard'))

    prediction = None
    earthquake_data = get_earthquake_data()
    if request.method == 'POST':
        hazard_type = request.form.get('hazard_type')
        rainfall = float(request.form.get('rainfall') or 0)
        river_level = float(request.form.get('river_level') or 0)
        humidity_pct = float(request.form.get('humidity_pct') or 0)
        population_density = float(request.form.get('population_density') or 0)

        prediction = predict_hazard(
            hazard_type=hazard_type,
            rainfall_mm=rainfall,
            river_level_m=river_level,
            humidity_pct=humidity_pct,
            population_density=population_density,
            earthquake_data=earthquake_data,
        )

        user = User.query.filter_by(username=session['username']).first()
        if user:
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                rainfall_mm=rainfall,
                river_level_m=river_level,
                humidity_pct=humidity_pct,
                population_density=population_density,
                score=prediction.get('score'),
                level=prediction.get('level'),
                message=prediction.get('message', 'Manual incident report created.'),
                alert=prediction.get('alert', False),
                status='REVIEWED' if prediction else 'NEW',
                reported_by='ai_prediction',
            )
            db.session.add(incident)
            try:
                if prediction:
                    ai_recommendation = AIRecommendation(
                        incident=incident,
                        user_id=user.id,
                        provider=prediction.get('provider'),
                        model=prediction.get('model'),
                        recommendation_type='hazard_prediction',
                        summary=prediction.get('message', '').strip() or 'AI hazard prediction generated.',
                        confidence_score=prediction.get('confidence'),
                        recommended_agencies=json.dumps(prediction.get('recommended_agencies', [])),
                        recommended_resources=json.dumps(prediction.get('recommended_resources', [])),
                        primary_factors=json.dumps(prediction.get('primary_factors', [])),
                    )
                    db.session.add(ai_recommendation)
                    db.session.flush()
                    audit_event = AuditEvent(
                        user_id=user.id,
                        entity_type='AIRecommendation',
                        entity_id=ai_recommendation.id,
                        action='CREATED',
                        details=(
                            f"AI prediction created from provider={prediction.get('provider')} "
                            f"model={prediction.get('model')} score={prediction.get('score')} "
                            f"for incident_id={incident.id}"
                        )
                    )
                    db.session.add(audit_event)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(str(e), 'error')

    total_active_alerts = Incident.query.filter_by(alert=True).count()
    total_incidents = Incident.query.count()
    latest_incident = Incident.query.order_by(Incident.created_at.desc()).first()
    latest_risk_score = latest_incident.score if latest_incident else 0

    latest_earthquake_magnitude = 0
    if earthquake_data and len(earthquake_data) > 0:
        latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

    return render_template('pages/ai_prediction.html',
                         prediction=prediction,
                         total_active_alerts=total_active_alerts,
                         total_incidents=total_incidents,
                         latest_risk_score=latest_risk_score,
                         latest_earthquake_magnitude=latest_earthquake_magnitude)
