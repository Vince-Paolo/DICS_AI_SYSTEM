from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from models import AuditEvent, db, Incident, IncidentResponse, Resource, ResourceRequest, Alert, Report, User, Task, utcnow
from blueprints.common import is_eoc_staff, current_user
from services import permissions as permission_service

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


@eoc_bp.route('/eoc/sos-incidents/pending')
def pending_sos_incidents():
    """Polled from the EOC dashboard so a citizen's Emergency SOS actually
    reaches a human, not just a database row -- see emergency_sos() in
    blueprints/citizen.py, which creates the Incident this queries for but
    (as of this endpoint being added) still had nothing on the other end
    watching for it in real time.

    An SOS incident stops showing up here the moment any EOC staffer
    verifies it (the existing verify_incident() action below, which moves
    status off 'NEW') -- reusing that real workflow signal rather than
    adding a separate, purely cosmetic 'dismiss' state that could let a
    genuine emergency get silently marked seen without anyone actually
    acting on it.
    """
    if not is_eoc_staff():
        return {'error': 'Unauthorized'}, 401

    pending = Incident.query.filter_by(hazard_type='EMERGENCY', status='NEW').order_by(Incident.created_at.desc()).all()
    return {
        'incidents': [
            {
                'id': incident.id,
                'location': incident.location or 'Location unavailable',
                'created_at': incident.created_at.isoformat() if incident.created_at else None,
            }
            for incident in pending
        ]
    }


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


@eoc_bp.route('/admin/alerts/<int:incident_id>/toggle', methods=['POST'])
def toggle_alert(incident_id):
    """Dispatch: toggle an incident's public alert status."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    incident = db.get_or_404(Incident, incident_id)
    incident.alert = not incident.alert
    verifier = User.query.filter_by(username=session['username']).first()
    try:
        db.session.flush()
        audit_event = AuditEvent(
            user_id=verifier.id if verifier else None,
            entity_type='Incident',
            entity_id=incident.id,
            action='ALERT_TOGGLED',
            details=(
                f"Alert toggled to {incident.alert} for incident {incident.id} "
                f"by {verifier.username if verifier else 'unknown'}"
            )
        )
        db.session.add(audit_event)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('admin.admin_alerts'))
    flash('Alert status updated.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@eoc_bp.route('/admin/incidents/<int:incident_id>/verify', methods=['POST'])
def verify_incident(incident_id):
    """Dispatch: verify that a reported incident is legitimate."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    incident = db.get_or_404(Incident, incident_id)
    verifier = User.query.filter_by(username=session['username']).first()
    incident.status = 'VERIFIED'
    incident.verified_by_id = verifier.id if verifier else None

    try:
        db.session.flush()
        audit_event = AuditEvent(
            user_id=verifier.id if verifier else None,
            entity_type='Incident',
            entity_id=incident.id,
            action='VERIFIED',
            details=(
                f"Incident {incident.id} verified by {verifier.username if verifier else 'unknown'}"
            )
        )
        db.session.add(audit_event)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('admin.admin_alerts'))

    flash('Incident marked as verified.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@eoc_bp.route('/admin/incidents/<int:incident_id>/assign-commander', methods=['POST'])
def assign_commander(incident_id):
    """Dispatch: assign a commander to an unresponded incident, creating an IncidentResponse."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('admin.admin_alerts'))

    incident = db.get_or_404(Incident, incident_id)

    existing = IncidentResponse.query.filter_by(incident_id=incident_id).first()
    if existing:
        flash('An incident response already exists for this incident.', 'warning')
        return redirect(url_for('admin.admin_alerts'))

    commander_id = request.form.get('commander_id', type=int)
    if not commander_id:
        flash('Please select a commander.', 'error')
        return redirect(url_for('admin.admin_alerts'))

    commander = db.session.get(User, commander_id)
    if not commander or commander.role != 'incident_commander':
        flash('Invalid commander selected.', 'error')
        return redirect(url_for('admin.admin_alerts'))

    response = IncidentResponse(
        incident_id=incident_id,
        commander_id=commander_id,
        status='ACTIVE',
        situation_summary=f'Response initiated by dispatch for {incident.hazard_type} at {incident.location}',
        priority_level='CRITICAL' if incident.level == 'CRITICAL' else 'HIGH' if incident.level == 'HIGH' else 'MEDIUM'
    )
    db.session.add(response)
    try:
        db.session.flush()
        audit_event = AuditEvent(
            user_id=commander.id,
            entity_type='IncidentResponse',
            entity_id=response.id,
            action='ASSIGNED',
            details=(
                f"Commander {commander.username} assigned to incident {incident.id} "
                f"via dispatch by {session.get('username', 'unknown')}"
            )
        )
        db.session.add(audit_event)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Failed to assign commander')
        flash('Unable to assign the commander. Please try again.', 'error')
        return redirect(url_for('admin.admin_alerts'))

    flash(f'Commander "{commander.full_name or commander.username}" assigned to incident #{incident_id}.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@eoc_bp.route('/admin/responses/<int:response_id>/transfer', methods=['POST'])
def transfer_commander(response_id):
    """Dispatch: transfer an active response to a different commander."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('admin.admin_responses'))

    response = db.get_or_404(IncidentResponse, response_id)
    new_commander_id = request.form.get('commander_id', type=int)

    if not new_commander_id:
        flash('Please select a commander.', 'error')
        return redirect(url_for('admin.admin_responses'))

    new_commander = db.session.get(User, new_commander_id)
    if not new_commander or new_commander.role != 'incident_commander':
        flash('Invalid commander selected.', 'error')
        return redirect(url_for('admin.admin_responses'))

    old_commander_name = response.commander.username if response.commander else 'Unknown'
    response.commander_id = new_commander_id
    try:
        db.session.flush()
        audit_event = AuditEvent(
            user_id=new_commander.id,
            entity_type='IncidentResponse',
            entity_id=response.id,
            action='TRANSFERRED',
            details=(
                f"Response {response.id} transferred from {old_commander_name} "
                f"to {new_commander.username} by {session.get('username', 'unknown')}"
            )
        )
        db.session.add(audit_event)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Failed to transfer commander')
        flash('Unable to transfer the commander. Please try again.', 'error')
        return redirect(url_for('admin.admin_responses'))

    flash(f'Response #{response_id} transferred from {old_commander_name} to {new_commander.username}.', 'success')
    return redirect(url_for('admin.admin_responses'))


@eoc_bp.route('/eoc/resource-requests')
def eoc_resource_requests():
    """Review requests submitted by agency coordinators and decide on them."""
    if not permission_service.can_decide_resource_request(current_user()):
        flash('You do not have permission to review resource requests.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', 'OPEN').strip().upper()
    query = ResourceRequest.query
    user = current_user()
    if permission_service.is_commander():
        query = query.join(Incident).join(IncidentResponse).filter(
            IncidentResponse.commander_id == user.id
        )
    if status_filter and status_filter != 'ALL':
        query = query.filter(ResourceRequest.status == status_filter)
    requests_ = query.order_by(ResourceRequest.created_at.desc()).all()

    open_count = ResourceRequest.query.filter_by(status='OPEN').count()

    return render_template('pages/eoc_resource_requests.html',
        requests=requests_,
        status_filter=status_filter,
        open_count=open_count,
    )


@eoc_bp.route('/eoc/resource-requests/<int:request_id>/decide', methods=['POST'])
def eoc_decide_resource_request(request_id):
    user = current_user()
    if not permission_service.can_decide_resource_request(user):
        flash('You do not have permission to decide resource requests.', 'danger')
        return redirect(url_for('dashboard'))

    resource_request = db.get_or_404(ResourceRequest, request_id)
    if permission_service.is_commander():
        response = resource_request.incident.response if resource_request.incident else None
        if not response or response.commander_id != user.id:
            abort(403)
    decision = request.form.get('decision', '').strip().upper()
    notes = request.form.get('notes', '').strip()

    if decision not in ('APPROVED', 'DENIED', 'FULFILLED'):
        flash('Invalid decision.', 'error')
        return redirect(url_for('eoc.eoc_resource_requests'))

    resource_request.status = decision
    resource_request.decision_notes = notes or None
    resource_request.decided_by_id = user.id
    resource_request.decided_at = utcnow()

    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='ResourceRequest',
            entity_id=resource_request.id,
            action=f'DECISION_{decision}',
            details=(
                f'Request #{resource_request.id} ({resource_request.quantity}x '
                f'{resource_request.resource_type} for {resource_request.agency}) '
                f'marked {decision} by {user.username}.'
            ),
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.eoc_resource_requests'))

    flash(f'Request #{resource_request.id} marked {decision.title()}.', 'success')
    return redirect(url_for('eoc.eoc_resource_requests'))


@eoc_bp.route('/eoc/alerts')
def official_alerts():
    """Official, citizen-facing alerts -- separate from the automatic AI risk
    flag on Incident.alert, which stays an internal signal on the
    verification page. Viewable by EOC/Commander/Coordinator; Admin is
    intentionally excluded (pure administration, not incident operations).
    Issuing/resolving is gated to can_issue_alert (EOC/Commander)."""
    if not permission_service.has_any_role('EOC', 'COMMANDER', 'COORDINATOR'):
        flash('You do not have permission to view alerts.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', 'ACTIVE').strip().upper()
    query = Alert.query
    if status_filter and status_filter != 'ALL':
        query = query.filter(Alert.status == status_filter)
    alerts = query.order_by(Alert.created_at.desc()).all()

    recent_incidents = Incident.query.order_by(Incident.created_at.desc()).limit(30).all()

    return render_template('pages/official_alerts.html',
        alerts=alerts,
        status_filter=status_filter,
        recent_incidents=recent_incidents,
        can_issue=permission_service.can_issue_alert(current_user()),
    )


@eoc_bp.route('/eoc/alerts/issue', methods=['POST'])
def issue_alert():
    """Publish an official, citizen-facing alert -- distinct from the
    automatic AI risk flag (Incident.alert), which stays as an internal
    operational signal, not a published advisory."""
    user = current_user()
    if not permission_service.can_issue_alert(user):
        flash('You do not have permission to issue alerts.', 'danger')
        return redirect(url_for('dashboard'))

    incident_id = request.form.get('incident_id', type=int)
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    severity = request.form.get('severity', 'MEDIUM').strip().upper()

    if not title or not message:
        flash('Alert title and message are required.', 'error')
        return redirect(url_for('eoc.official_alerts'))

    if severity not in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'):
        severity = 'MEDIUM'

    alert = Alert(
        incident_id=incident_id or None,
        user_id=user.id,
        title=title,
        message=message,
        severity=severity,
        status='ACTIVE',
    )
    db.session.add(alert)
    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='Alert',
            entity_id=alert.id,
            action='ISSUED',
            details=f'Alert "{title}" ({severity}) issued by {user.username}.',
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.official_alerts'))

    flash(f'Alert "{title}" published.', 'success')
    return redirect(url_for('eoc.official_alerts'))


@eoc_bp.route('/eoc/alerts/<int:alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    user = current_user()
    if not permission_service.can_issue_alert(user):
        flash('You do not have permission to resolve alerts.', 'danger')
        return redirect(url_for('dashboard'))

    alert = db.get_or_404(Alert, alert_id)
    alert.status = 'RESOLVED'
    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='Alert',
            entity_id=alert.id,
            action='RESOLVED',
            details=f'Alert #{alert.id} ("{alert.title}") resolved by {user.username}.',
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.official_alerts'))

    flash('Alert resolved.', 'success')
    return redirect(url_for('eoc.official_alerts'))


@eoc_bp.route('/eoc/incidents/<int:incident_id>')
def eoc_incident_detail(incident_id):
    """Single-incident detail view for EOC staff: full context plus the
    ability to log a dated report/note against the incident directly --
    useful during verification/triage, before any response is activated
    (IncidentMessage, by contrast, requires an active IncidentResponse)."""
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    incident = db.get_or_404(Incident, incident_id)
    reports = Report.query.filter_by(incident_id=incident.id).order_by(Report.created_at.desc()).all()

    return render_template('pages/eoc_incident_detail.html',
        incident=incident,
        reports=reports,
        can_log_report=permission_service.can_log_incident_report(current_user()),
    )


@eoc_bp.route('/eoc/incidents/<int:incident_id>/log-report', methods=['POST'])
def log_incident_report(incident_id):
    user = current_user()
    if not permission_service.can_log_incident_report(user):
        flash('You do not have permission to log incident reports.', 'danger')
        return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))

    incident = db.get_or_404(Incident, incident_id)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    report_type = request.form.get('report_type', 'GENERAL').strip().upper()

    if not title or not content:
        flash('Report title and content are required.', 'error')
        return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))

    if report_type not in ('GENERAL', 'TRIAGE', 'VERIFICATION', 'ESCALATION'):
        report_type = 'GENERAL'

    report = Report(
        incident_id=incident.id,
        user_id=user.id,
        title=title,
        content=content,
        report_type=report_type,
    )
    db.session.add(report)
    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='Report',
            entity_id=report.id,
            action='LOGGED',
            details=f'Report "{title}" ({report_type}) logged for incident #{incident.id} by {user.username}.',
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))

    flash(f'Report "{title}" logged.', 'success')
    return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))
