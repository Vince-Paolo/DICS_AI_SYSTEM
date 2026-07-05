from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import db, User, Incident, IncidentResponse, Task, Resource
from blueprints.common import is_eoc_staff

eoc_bp = Blueprint('eoc', __name__)


@eoc_bp.route('/eoc-dashboard')
def eoc_dashboard():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    critical_incidents = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).order_by(Incident.created_at.desc()).limit(10).all()

    recent_incidents = db.session.query(Incident).order_by(
        Incident.created_at.desc()
    ).limit(8).all()

    total_active = len(active_responses)
    total_critical = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).count()
    total_tasks = db.session.query(Task).filter(
        Task.status.in_(['PENDING', 'IN_PROGRESS'])
    ).count()
    total_resources = db.session.query(Resource).filter(
        Resource.status == 'DEPLOYED'
    ).count()
    total_incidents_all = db.session.query(Incident).count()

    hazard_breakdown = db.session.query(
        Incident.hazard_type, db.func.count(Incident.id)
    ).group_by(Incident.hazard_type).order_by(db.func.count(Incident.id).desc()).all()

    return render_template('pages/eoc_dashboard.html',
                         active_responses=active_responses,
                         critical_incidents=critical_incidents,
                         recent_incidents=recent_incidents,
                         total_active=total_active,
                         total_critical=total_critical,
                         total_tasks=total_tasks,
                         total_resources=total_resources,
                         total_incidents_all=total_incidents_all,
                         hazard_breakdown=hazard_breakdown)


@eoc_bp.route('/eoc/incidents')
def eoc_incident_monitoring():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    level_filter = request.args.get('level', '').strip()
    hazard_filter = request.args.get('hazard', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = db.session.query(Incident)
    if level_filter:
        query = query.filter(Incident.level == level_filter)
    if hazard_filter:
        query = query.filter(Incident.hazard_type == hazard_filter)
    if status_filter == 'responded':
        query = query.join(IncidentResponse, isouter=False)
    elif status_filter == 'unresponded':
        query = query.outerjoin(IncidentResponse).filter(IncidentResponse.id.is_(None))
    elif status_filter == 'alert':
        query = query.filter(Incident.alert.is_(True))

    incidents = query.order_by(Incident.created_at.desc()).all()

    hazard_types = [row[0] for row in db.session.query(Incident.hazard_type).distinct().order_by(Incident.hazard_type).all()]

    total_incidents = db.session.query(Incident).count()
    total_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()
    total_critical = db.session.query(Incident).filter(Incident.level == 'CRITICAL').count()
    total_unresponded = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).outerjoin(IncidentResponse).filter(IncidentResponse.id.is_(None)).count()

    return render_template('pages/eoc_incident_monitoring.html',
                         incidents=incidents,
                         hazard_types=hazard_types,
                         level_filter=level_filter,
                         hazard_filter=hazard_filter,
                         status_filter=status_filter,
                         total_incidents=total_incidents,
                         total_alerts=total_alerts,
                         total_critical=total_critical,
                         total_unresponded=total_unresponded)


@eoc_bp.route('/eoc/resources')
def eoc_resource_monitoring():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', '').strip()
    agency_filter = request.args.get('agency', '').strip()

    query = db.session.query(Resource).join(IncidentResponse).join(Incident)
    if status_filter:
        query = query.filter(Resource.status == status_filter)
    if agency_filter:
        query = query.filter(Resource.agency == agency_filter)

    resources = query.order_by(Resource.allocated_at.desc()).all()

    agencies = [row[0] for row in db.session.query(Resource.agency).distinct().order_by(Resource.agency).all()]

    total_deployed = db.session.query(Resource).filter(Resource.status == 'DEPLOYED').count()
    total_available = db.session.query(Resource).filter(Resource.status == 'AVAILABLE').count()
    total_returning = db.session.query(Resource).filter(Resource.status == 'RETURNING').count()
    total_units = db.session.query(db.func.coalesce(db.func.sum(Resource.quantity), 0)).filter(
        Resource.status == 'DEPLOYED'
    ).scalar()

    agency_breakdown = db.session.query(
        Resource.agency, db.func.count(Resource.id)
    ).filter(Resource.status == 'DEPLOYED').group_by(Resource.agency).order_by(db.func.count(Resource.id).desc()).all()

    return render_template('pages/eoc_resource_monitoring.html',
                         resources=resources,
                         agencies=agencies,
                         status_filter=status_filter,
                         agency_filter=agency_filter,
                         total_deployed=total_deployed,
                         total_available=total_available,
                         total_returning=total_returning,
                         total_units=total_units,
                         agency_breakdown=agency_breakdown)
