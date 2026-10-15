import re
from datetime import timezone

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from sqlalchemy import and_
from sqlalchemy.orm import aliased

from models import AIRecommendation, AuditEvent, BarangayContactLog, BarangayResponseUnit, Barangay, db, Incident, IncidentResponse, Municipality, PatientRecord, Resource, Alert, Report, ResponseTeam, User, Task, utcnow
from blueprints.common import is_eoc_staff, current_user
from services import permissions as permission_service
from services.response_decision_support import (
    OUTCOMES,
    build_response_assessment,
    is_assessable_response_incident,
    sign_response_assessment,
)

eoc_bp = Blueprint('eoc', __name__)


def _require_emergency_log_access():
    if not (permission_service.is_imt() or permission_service.is_dispatcher()):
        abort(403)


def _get_emergency_incident(incident_id):
    incident = db.get_or_404(Incident, incident_id)
    if incident.hazard_type != 'EMERGENCY':
        abort(404)
    return incident


def _timeline_sort_key(event):
    occurred_at = event['occurred_at']
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.timestamp()


@eoc_bp.route('/eoc/emergency-log')
def emergency_log():
    _require_emergency_log_access()
    search = request.args.get('q', '').strip()
    query = Incident.query.filter_by(hazard_type='EMERGENCY')
    if search:
        term = f'%{search}%'
        query = query.filter(
            db.or_(
                Incident.control_number.ilike(term),
                Incident.location.ilike(term),
            )
        )

    incidents = query.order_by(
        db.cast(db.func.substr(Incident.control_number, 1, 4), db.Integer).desc(),
        db.cast(db.func.substr(Incident.control_number, 6), db.Integer).desc(),
    ).all()
    barangays = Barangay.query.join(Municipality).filter(
        Municipality.code == '043424'
    ).order_by(Barangay.name).all()
    return render_template(
        'pages/eoc_emergency_log.html',
        incidents=incidents,
        barangays=barangays,
        search=search,
        can_create_emergency=permission_service.is_dispatcher(),
    )


@eoc_bp.route('/eoc/emergency-search')
def emergency_search():
    _require_emergency_log_access()
    control_number = request.args.get('q', '').strip().upper()
    if not re.fullmatch(r'\d{4}-\d{5}', control_number):
        flash(_('Enter a valid control number, for example 2026-00001.'), 'warning')
        return redirect(url_for('eoc.emergency_log'))

    incident = Incident.query.filter_by(control_number=control_number).first()
    if incident is None:
        flash(_('No incident was found with control number %(control_number)s.') % {
            'control_number': control_number,
        }, 'info')
        return redirect(url_for('eoc.emergency_log'))
    if incident.hazard_type != 'EMERGENCY':
        flash(_(
            'Control number %(control_number)s belongs to a %(incident_type)s record, '
            'not a dispatcher-logged emergency. This search only opens dispatcher-logged emergencies.'
        ) % {
            'control_number': control_number,
            'incident_type': incident.hazard_type,
        }, 'info')
        return redirect(url_for('eoc.emergency_log'))
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>')
def emergency_log_detail(incident_id):
    _require_emergency_log_access()
    incident = db.get_or_404(Incident, incident_id)
    if not is_assessable_response_incident(incident):
        abort(404)
    is_emergency_record = incident.hazard_type == 'EMERGENCY'
    is_dispatcher = permission_service.is_dispatcher()
    can_edit_emergency_assessment = is_emergency_record and (
        is_dispatcher or permission_service.is_imt()
    )
    can_dispatch = is_dispatcher and is_emergency_record
    is_open = incident.status not in {'REJECTED', 'RESOLVED', 'CLOSED'} and not (
        incident.response and incident.response.status in {'CLOSED', 'RESOLVED'}
    )
    response_assessment = build_response_assessment(incident) if is_open else None
    response_decisions = AIRecommendation.query.filter_by(
        incident_id=incident.id,
        recommendation_type='response_decision_support',
    ).order_by(AIRecommendation.created_at.desc()).limit(10).all()
    dispatch_responders = []
    response_teams = []
    response_units = []
    contact_logs = []
    barangays = []
    if can_edit_emergency_assessment:
        barangays = Barangay.query.join(Municipality).filter(
            Municipality.code == '043424'
        ).order_by(Barangay.name).all()
    if can_dispatch:
        dispatch_responders = User.query.filter_by(
            role='field_responder',
            is_disabled=False,
        ).order_by(User.full_name, User.username).all()
        response_teams = [
            team for team in ResponseTeam.query.order_by(ResponseTeam.name).all()
            if any(
                member.role == 'field_responder'
                and not member.is_disabled
                for member in team.members
            )
        ]
    if is_dispatcher:
        response_units = BarangayResponseUnit.query.join(Barangay).join(Municipality).filter(
            Municipality.code == '043424'
        ).order_by(Barangay.name).all()
        contact_logs = BarangayContactLog.query.filter_by(
            incident_id=incident.id,
        ).order_by(BarangayContactLog.contacted_at.desc()).all()
    timeline_events = []

    def add_timeline_event(occurred_at, title, detail, icon):
        if occurred_at:
            timeline_events.append({
                'occurred_at': occurred_at,
                'title': title,
                'detail': detail,
                'icon': icon,
            })

    add_timeline_event(
        incident.created_at,
        'Emergency logged',
        incident.control_number,
        'bi-plus-circle',
    )
    if incident.event_time and incident.event_time != incident.created_at:
        add_timeline_event(
            incident.event_time,
            'Reported event time',
            'Time the emergency was reported to have occurred.',
            'bi-clock',
        )
    if incident.response:
        add_timeline_event(
            incident.response.started_at,
            'Response started',
            incident.response.status,
            'bi-broadcast-pin',
        )
        for task in incident.response.tasks:
            add_timeline_event(
                task.created_at,
                'Dispatch task assigned',
                f'{task.title} · {task.status}',
                'bi-list-task',
            )
    for entry in response_decisions:
        add_timeline_event(
            entry.created_at,
            'Dispatcher decision recorded',
            OUTCOMES.get(entry.decision, entry.decision or 'Not recorded'),
            'bi-clipboard2-check',
        )
    for log in contact_logs:
        add_timeline_event(
            log.contacted_at,
            'Barangay contacted',
            f'{log.response_unit.barangay.name} · {log.outcome.replace("_", " ").title()} via {log.contact_method.lower()}',
            'bi-telephone',
        )
    for patient in incident.patient_records:
        add_timeline_event(
            patient.created_at,
            'Patient record added',
            None,
            'bi-heart-pulse',
        )
        if patient.updated_at and patient.updated_at != patient.created_at:
            add_timeline_event(
                patient.updated_at,
                'Patient record updated',
                None,
                'bi-pencil',
            )
    timeline_events.sort(key=_timeline_sort_key, reverse=True)
    return render_template(
        'pages/eoc_emergency_detail.html',
        incident=incident,
        can_dispatch=can_dispatch,
        can_log_contact=is_dispatcher,
        can_record_decision=is_dispatcher,
        can_edit_emergency_assessment=can_edit_emergency_assessment,
        barangays=barangays,
        is_emergency_record=is_emergency_record,
        response_assessment=response_assessment,
        response_assessment_token=(
            sign_response_assessment(incident.id, response_assessment)
            if response_assessment and is_dispatcher else None
        ),
        response_decisions=response_decisions,
        response_outcomes=OUTCOMES,
        dispatch_responders=dispatch_responders,
        response_teams=response_teams,
        response_units=response_units,
        contact_logs=contact_logs,
        timeline_events=timeline_events[:50],
    )


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>/assessment', methods=['POST'])
def update_emergency_assessment(incident_id):
    _require_emergency_log_access()
    incident = _get_emergency_incident(incident_id)
    severity = request.form.get('severity', '').strip()
    barangay_id = request.form.get('barangay_id', type=int)
    if severity not in {'Low', 'Moderate', 'High', 'Severe'} or barangay_id is None:
        flash('Select a valid severity and San Pablo barangay.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    barangay = Barangay.query.join(Municipality).filter(
        Barangay.id == barangay_id,
        Municipality.code == '043424',
    ).first()
    if barangay is None:
        flash('Select a barangay within San Pablo City.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    old_severity = incident.level
    old_barangay = db.session.get(Barangay, incident.barangay_id) if incident.barangay_id else None
    changes = []
    if old_severity != severity:
        changes.append(f'Severity changed from {old_severity or "not recorded"} to {severity}.')
    if incident.barangay_id != barangay.id:
        changes.append(
            f'Barangay changed from {old_barangay.name if old_barangay else "not recorded"} '
            f'to {barangay.name}.'
        )

    incident.level = severity
    incident.barangay_id = barangay.id
    incident.municipality_id = barangay.municipality_id
    if changes:
        editor = current_user()
        db.session.add(AuditEvent(
            user_id=editor.id if editor else None,
            entity_type='Incident',
            entity_id=incident.id,
            action='ASSESSMENT_FIELDS_UPDATED',
            details=' '.join(changes),
        ))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to update emergency assessment fields')
        flash('Unable to update severity and barangay. Please try again.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    flash('Emergency severity and barangay updated.', 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>/patient-count', methods=['POST'])
def update_emergency_patient_count(incident_id):
    _require_emergency_log_access()
    incident = _get_emergency_incident(incident_id)
    patient_count = request.form.get('patient_count', type=int)
    if patient_count is None or patient_count < len(incident.patient_records):
        flash('Patient count must be a non-negative number and cannot be less than the recorded patients.', 'error')
    else:
        incident.patient_count = patient_count
        db.session.commit()
        flash('Patient count updated.', 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>/patients', methods=['POST'])
def add_emergency_patient(incident_id):
    _require_emergency_log_access()
    incident = _get_emergency_incident(incident_id)
    condition = request.form.get('condition', '').strip()
    destination = request.form.get('destination', '').strip()
    status = request.form.get('status', 'RECEIVING_CARE').strip().upper()
    if not condition or len(condition) > 255 or len(destination) > 255 or not status or len(status) > 50:
        flash('Condition and a status of 50 characters or fewer are required; destination must be 255 characters or fewer.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    incident.patient_records.append(PatientRecord(
        condition=condition,
        destination=destination or None,
        status=status,
    ))
    incident.patient_count = max(incident.patient_count, len(incident.patient_records))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to add an emergency patient record')
        flash('Unable to save the patient record. Please try again.', 'error')
    else:
        flash('Patient record added.', 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>/patients/<int:patient_id>', methods=['POST'])
def update_emergency_patient(incident_id, patient_id):
    _require_emergency_log_access()
    incident = _get_emergency_incident(incident_id)
    patient = db.get_or_404(PatientRecord, patient_id)
    if patient.incident_id != incident.id:
        abort(404)

    condition = request.form.get('condition', '').strip()
    destination = request.form.get('destination', '').strip()
    status = request.form.get('status', '').strip().upper()
    if not condition or len(condition) > 255 or len(destination) > 255 or not status or len(status) > 50:
        flash('Condition and a status of 50 characters or fewer are required; destination must be 255 characters or fewer.', 'error')
    else:
        patient.condition = condition
        patient.destination = destination or None
        patient.status = status
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update an emergency patient record')
            flash('Unable to update the patient record. Please try again.', 'error')
        else:
            flash('Patient record updated.', 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc/emergency-log/<int:incident_id>/patients/<int:patient_id>/delete', methods=['POST'])
def delete_emergency_patient(incident_id, patient_id):
    _require_emergency_log_access()
    incident = _get_emergency_incident(incident_id)
    patient = db.get_or_404(PatientRecord, patient_id)
    if patient.incident_id != incident.id:
        abort(404)
    db.session.delete(patient)
    db.session.commit()
    flash('Patient record deleted.', 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@eoc_bp.route('/eoc-dashboard')
def eoc_dashboard():
    if not is_eoc_staff():
        flash('EOC Staff access required.', 'danger')
        return redirect(url_for('dashboard'))

    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(IncidentResponse.started_at.desc()).all()

    critical_incidents = db.session.query(Incident).filter(
        Incident.level.in_(['Severe', 'High'])
    ).order_by(Incident.created_at.desc()).limit(10).all()

    recent_incidents = db.session.query(Incident).order_by(
        Incident.created_at.desc()
    ).limit(8).all()

    total_active = len(active_responses)
    total_critical = db.session.query(Incident).filter(
        Incident.level.in_(['Severe', 'High'])
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

    view_filter = request.args.get('view', 'active').strip().lower()
    if view_filter not in {'active', 'verification', 'closed'}:
        view_filter = 'active'
    level_filter = request.args.get('level', '').strip()
    hazard_filter = request.args.get('hazard', '').strip()
    status_filter = request.args.get('status', '').strip()

    query = db.session.query(Incident)
    closed_statuses = ['REJECTED', 'RESOLVED', 'CLOSED']
    closed_responses = ['RESOLVED', 'CLOSED']
    if view_filter == 'verification':
        query = query.filter(Incident.status == 'NEW')
    elif view_filter == 'closed':
        query = query.filter(db.or_(
            Incident.status.in_(closed_statuses),
            Incident.response.has(IncidentResponse.status.in_(closed_responses)),
        ))
    else:
        query = query.filter(
            db.or_(
                Incident.status.is_(None),
                ~Incident.status.in_(['NEW', *closed_statuses]),
            ),
            ~Incident.response.has(IncidentResponse.status.in_(closed_responses)),
        )
    if level_filter:
        query = query.filter(Incident.level == level_filter)
    if hazard_filter:
        query = query.filter(Incident.hazard_type == hazard_filter)
    if status_filter == 'responded':
        query = query.join(IncidentResponse).filter(
            IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
        )
    elif status_filter == 'unresponded':
        active_response = aliased(IncidentResponse)
        query = query.outerjoin(
            active_response,
            and_(
                active_response.incident_id == Incident.id,
                active_response.status.in_(['ACTIVE', 'MONITORING'])
            )
        ).filter(active_response.id.is_(None))
    elif status_filter == 'alert':
        query = query.filter(Incident.alert.is_(True))

    incidents = query.order_by(Incident.created_at.desc()).all()

    hazard_types = [row[0] for row in db.session.query(Incident.hazard_type).distinct().order_by(Incident.hazard_type).all()]

    total_incidents = db.session.query(Incident).count()
    total_alerts = db.session.query(Incident).filter(Incident.alert.is_(True)).count()
    total_critical = db.session.query(Incident).filter(
        Incident.level.in_(['Severe', 'High'])
    ).count()
    active_response = aliased(IncidentResponse)
    total_unresponded = db.session.query(Incident).filter(
        Incident.level.in_(['Severe', 'High'])
    ).outerjoin(
        active_response,
        and_(
            active_response.incident_id == Incident.id,
            active_response.status.in_(['ACTIVE', 'MONITORING'])
        )
    ).filter(active_response.id.is_(None)).count()

    tab_counts = {
        'active': Incident.query.filter(
            db.or_(
                Incident.status.is_(None),
                ~Incident.status.in_(['NEW', *closed_statuses]),
            ),
            ~Incident.response.has(IncidentResponse.status.in_(closed_responses)),
        ).count(),
        'verification': Incident.query.filter_by(status='NEW').count(),
        'closed': Incident.query.filter(db.or_(
            Incident.status.in_(closed_statuses),
            Incident.response.has(IncidentResponse.status.in_(closed_responses)),
        )).count(),
    }

    return render_template('pages/eoc_incident_monitoring.html',
                         incidents=incidents,
                         hazard_types=hazard_types,
                         view_filter=view_filter,
                         tab_counts=tab_counts,
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
    query = db.session.query(Resource).join(IncidentResponse).join(Incident)
    if status_filter:
        query = query.filter(Resource.status == status_filter)

    resources = query.order_by(Resource.allocated_at.desc()).all()

    total_deployed = db.session.query(Resource).filter(Resource.status == 'DEPLOYED').count()
    total_available = db.session.query(Resource).filter(Resource.status == 'AVAILABLE').count()
    total_returning = db.session.query(Resource).filter(Resource.status == 'RETURNING').count()
    total_units = db.session.query(db.func.coalesce(db.func.sum(Resource.quantity), 0)).filter(
        Resource.status == 'DEPLOYED'
    ).scalar()

    return render_template('pages/eoc_resource_monitoring.html',
                         resources=resources,
                         status_filter=status_filter,
                         total_deployed=total_deployed,
                         total_available=total_available,
                         total_returning=total_returning,
                         total_units=total_units)


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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('admin.eoc_verifications'))
    flash('Alert status updated.', 'success')
    return redirect(url_for('admin.eoc_verifications'))


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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    flash('Incident marked as verified.', 'success')
    return redirect(url_for('admin.eoc_verifications'))


@eoc_bp.route('/admin/incidents/<int:incident_id>/reject', methods=['POST'])
def reject_incident(incident_id):
    user = current_user()
    if not user or not permission_service.has_any_role('IMT'):
        return {'error': 'Unauthorized'}, 403

    incident = db.get_or_404(Incident, incident_id)
    response = incident.response
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('A reason is required to mark an incident as a false alarm.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    incident.status = 'REJECTED'
    incident.alert = False
    if response and response.status in {'ACTIVE', 'MONITORING'}:
        response.status = 'CLOSED'
        response.resolved_at = utcnow()
        response.closed_at = utcnow()
    recommendation = AIRecommendation.query.filter_by(
        incident_id=incident.id,
        recommendation_type='hazard_prediction',
    ).order_by(AIRecommendation.created_at.desc()).first()
    try:
        if recommendation:
            recommendation.decision = 'REJECTED'
            recommendation.decision_reason = reason
            recommendation.decided_by_id = user.id
            recommendation.decided_at = utcnow()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='Incident',
            entity_id=incident.id,
            action='REJECTED',
            details=f'Incident {incident.id} marked REJECTED as a false alarm by {user.username}: {reason}',
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to reject incident')
        flash('Unable to mark the incident as rejected. Please try again.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    flash('Incident marked as rejected false alarm.', 'success')
    return redirect(url_for('eoc.eoc_dashboard'))


@eoc_bp.route('/admin/incidents/<int:incident_id>/assign-commander', methods=['POST'])
def assign_commander(incident_id):
    """Dispatch: assign a commander to an unresponded incident, creating an IncidentResponse."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('admin.eoc_verifications'))

    incident = db.get_or_404(Incident, incident_id)

    existing = IncidentResponse.query.filter_by(incident_id=incident_id).first()
    if existing:
        flash('An incident response already exists for this incident.', 'warning')
        return redirect(url_for('admin.eoc_verifications'))

    commander_id = request.form.get('commander_id', type=int)
    if not commander_id:
        flash('Please select a commander.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    commander = db.session.get(User, commander_id)
    if (
        not commander
        or commander.is_disabled
        or not permission_service.user_has_any_role(commander, 'IMT')
    ):
        flash('Invalid commander selected.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    response = IncidentResponse(
        incident_id=incident_id,
        commander_id=commander_id,
        status='ACTIVE',
        situation_summary=f'Response initiated by dispatch for {incident.hazard_type} at {incident.location}',
        priority_level='CRITICAL' if incident.level == 'Severe' else 'HIGH' if incident.level == 'High' else 'MEDIUM'
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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to assign commander')
        flash('Unable to assign the commander. Please try again.', 'error')
        return redirect(url_for('admin.eoc_verifications'))

    flash(_('Commander "%(name)s" assigned to incident #%(incident_id)s.') % {
        'name': commander.full_name or commander.username,
        'incident_id': incident_id,
    }, 'success')
    return redirect(url_for('admin.eoc_verifications'))


@eoc_bp.route('/admin/responses/<int:response_id>/transfer', methods=['POST'])
def transfer_commander(response_id):
    """Dispatch: transfer an active response to a different commander."""
    if not is_eoc_staff():
        flash('EOC staff access required.', 'danger')
        return redirect(url_for('admin.eoc_operations'))

    response = db.get_or_404(IncidentResponse, response_id)
    new_commander_id = request.form.get('commander_id', type=int)

    if not new_commander_id:
        flash('Please select a commander.', 'error')
        return redirect(url_for('admin.eoc_operations'))

    new_commander = db.session.get(User, new_commander_id)
    if (
        not new_commander
        or new_commander.is_disabled
        or not permission_service.user_has_any_role(new_commander, 'IMT')
    ):
        flash('Invalid commander selected.', 'error')
        return redirect(url_for('admin.eoc_operations'))

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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to transfer commander')
        flash('Unable to transfer the commander. Please try again.', 'error')
        return redirect(url_for('admin.eoc_operations'))

    flash(_('Response #%(response_id)s transferred from %(old_commander)s to %(new_commander)s.') % {
        'response_id': response_id,
        'old_commander': old_commander_name,
        'new_commander': new_commander.username,
    }, 'success')
    return redirect(url_for('admin.eoc_operations'))


@eoc_bp.route('/eoc/alerts')
def official_alerts():
    """Official, citizen-facing alerts are an IMT operational action."""
    if not permission_service.has_any_role('IMT'):
        flash('You do not have permission to view alerts.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', 'ACTIVE').strip().upper()
    query = Alert.query.filter_by(audience='PUBLIC')
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
        audience='PUBLIC',
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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.official_alerts'))

    flash(_('Alert "%(title)s" published.') % {'title': title}, 'success')
    return redirect(url_for('eoc.official_alerts'))


@eoc_bp.route('/eoc/alerts/<int:alert_id>/resolve', methods=['POST'])
def resolve_alert(alert_id):
    user = current_user()
    if not permission_service.can_issue_alert(user):
        flash('You do not have permission to resolve alerts.', 'danger')
        return redirect(url_for('dashboard'))

    alert = Alert.query.filter_by(id=alert_id, audience='PUBLIC').first_or_404()
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
    except Exception:
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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('EOC operation failed')
        flash('Unable to complete the EOC operation. Please try again.', 'error')
        return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))

    flash(_('Report "%(title)s" logged.') % {'title': title}, 'success')
    return redirect(url_for('eoc.eoc_incident_detail', incident_id=incident_id))
