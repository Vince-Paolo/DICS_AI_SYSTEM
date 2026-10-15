import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from itsdangerous import BadSignature, SignatureExpired
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy.orm import joinedload

from models import (
    BarangayContactLog,
    BarangayResponseUnit,
    Barangay,
    Alert,
    AIRecommendation,
    AuditEvent,
    Incident,
    IncidentResponse,
    Municipality,
    Resource,
    ResponseTeam,
    Task,
    User,
    db,
    utcnow,
)
from services import permissions as permission_service
from services.realtime_data import SAN_PABLO_CITY
from services.response_decision_support import (
    OUTCOMES,
    build_response_assessment,
    is_assessable_response_incident,
    load_response_assessment,
)


dispatcher_bp = Blueprint('dispatcher', __name__)
MANILA_TIMEZONE = ZoneInfo('Asia/Manila')
TASK_PRIORITIES = {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
CONTACT_METHODS = {'PHONE', 'RADIO'}
CONTACT_OUTCOMES = {'NO_ANSWER', 'CONTACTED', 'ASSISTANCE_DISPATCHED', 'UNAVAILABLE', 'OTHER'}


def _require_dispatcher():
    dispatcher = permission_service.current_user_for_roles('DISPATCHER')
    if dispatcher is None:
        abort(403)
    return dispatcher


def _require_dispatcher_or_imt():
    user = permission_service.current_user_for_roles('DISPATCHER', 'IMT')
    if user is None:
        abort(403)
    return user


def _get_emergency(incident_id):
    incident = db.get_or_404(Incident, incident_id)
    if incident.hazard_type != 'EMERGENCY':
        abort(404)
    return incident


def _cdrrmo_responders():
    return User.query.filter_by(
        role='field_responder',
        is_disabled=False,
    ).order_by(User.full_name, User.username).all()


@dispatcher_bp.route('/dispatcher-dashboard')
def dispatcher_dashboard():
    _require_dispatcher()
    local_now = datetime.now(timezone.utc).astimezone(MANILA_TIMEZONE)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = local_end.astimezone(timezone.utc).replace(tzinfo=None)

    emergency_incidents = Incident.query.options(
        joinedload(Incident.response),
    ).filter(
        Incident.hazard_type == 'EMERGENCY',
    ).order_by(Incident.created_at.desc()).all()
    active_incidents = [
        incident for incident in emergency_incidents
        if incident.status not in {'REJECTED', 'RESOLVED', 'CLOSED'}
        and not (
            incident.response
            and incident.response.status in {'RESOLVED', 'CLOSED'}
        )
    ]
    severity_order = {'severe': 0, 'high': 1, 'moderate': 2, 'low': 3}
    active_incidents.sort(
        key=lambda incident: (
            severity_order.get((incident.level or '').strip().lower(), len(severity_order)),
            incident.created_at or datetime.max,
        )
    )
    logged_today_count = sum(
        incident.created_at is not None
        and start_utc <= incident.created_at < end_utc
        for incident in emergency_incidents
    )

    active_assignments = db.session.query(
        Task.assigned_to_user_id,
        Task.assigned_to_team_id,
        IncidentResponse.incident_id,
    ).join(IncidentResponse).filter(
        Task.status.in_({'PENDING', 'IN_PROGRESS'}),
        IncidentResponse.status.notin_({'RESOLVED', 'CLOSED'}),
    ).all()
    assigned_incident_ids = {
        assignment.incident_id for assignment in active_assignments
    }
    busy_responder_ids = {
        assignment.assigned_to_user_id for assignment in active_assignments
        if assignment.assigned_to_user_id is not None
    }
    busy_team_ids = {
        assignment.assigned_to_team_id for assignment in active_assignments
        if assignment.assigned_to_team_id is not None
    }
    if busy_team_ids:
        busy_teams = ResponseTeam.query.filter(
            ResponseTeam.id.in_(busy_team_ids)
        ).all()
        busy_responder_ids.update(
            member.id
            for team in busy_teams
            for member in team.members
        )

    barangay_ids = {
        incident.barangay_id for incident in active_incidents
        if incident.barangay_id is not None
    }
    barangays = Barangay.query.filter(Barangay.id.in_(barangay_ids)).all() if barangay_ids else []
    barangay_names = {barangay.id: barangay.name for barangay in barangays}
    responders = _cdrrmo_responders()
    return render_template(
        'pages/dispatcher_dashboard.html',
        incidents=active_incidents,
        barangay_names=barangay_names,
        logged_today_count=logged_today_count,
        unassigned_count=sum(
            incident.id not in assigned_incident_ids
            for incident in active_incidents
        ),
        free_responder_count=sum(
            responder.id not in busy_responder_ids
            for responder in responders
        ),
        local_now=local_now,
        now_utc=datetime.now(timezone.utc).replace(tzinfo=None),
    )


@dispatcher_bp.route('/dispatcher/decision-support')
def decision_support():
    _require_dispatcher_or_imt()
    incidents = Incident.query.filter(
        db.or_(
            Incident.hazard_type == 'EMERGENCY',
            db.and_(
                Incident.reported_by == 'system',
                Incident.location == SAN_PABLO_CITY,
            ),
        )
    ).order_by(
        Incident.created_at.desc()
    ).all()
    open_incidents = [
        incident for incident in incidents
        if incident.status not in {'REJECTED', 'RESOLVED', 'CLOSED'}
        and not (
            incident.response
            and incident.response.status in {'CLOSED', 'RESOLVED'}
        )
    ]
    assessments = [
        (incident, build_response_assessment(incident))
        for incident in open_incidents
    ]
    assessments.sort(
        key=lambda item: item[1]['score'] if item[1]['score'] is not None else -1,
        reverse=True,
    )
    return render_template(
        'pages/dispatcher_decision_support.html',
        assessments=assessments,
    )


@dispatcher_bp.route(
    '/dispatcher/incidents/<int:incident_id>/decision',
    methods=['POST'],
)
def record_response_decision(incident_id):
    dispatcher = _require_dispatcher()
    incident = db.get_or_404(Incident, incident_id)
    if not is_assessable_response_incident(incident):
        abort(404)
    if incident.status in {'REJECTED', 'RESOLVED', 'CLOSED'} or (
        incident.response and incident.response.status in {'CLOSED', 'RESOLVED'}
    ):
        abort(409)

    choice = request.form.get('decision', '').strip().upper()
    reason = request.form.get('decision_reason', '').strip()
    if choice not in OUTCOMES:
        flash('Select one of the listed response decisions.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
    if len(reason) > 2000:
        flash('Decision reason must be 2,000 characters or fewer.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    assessment_token = request.form.get('assessment_token', '')
    try:
        assessment = load_response_assessment(assessment_token, incident.id)
    except (BadSignature, ValueError):
        flash('The displayed assessment expired or could not be verified. Review it again before recording a decision.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    if (assessment['outcome'] is None or choice != assessment['outcome']) and not reason:
        flash('A reason is required when information is insufficient or your decision differs from the suggestion.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    recommendation = AIRecommendation(
        incident_id=incident.id,
        user_id=dispatcher.id,
        provider='rules-based',
        model=assessment['rules_version'],
        recommendation_type='response_decision_support',
        summary=(
            (
                f"Suggested outcome: {assessment['outcome']} "
                f"({assessment['score']}/100 response-need points)."
                if assessment['outcome'] is not None
                else 'Suggested outcome withheld: insufficient severity or location information.'
            )
        ),
        primary_factors=json.dumps(assessment['factors']),
        decision=choice,
        decision_reason=reason or None,
        decided_by_id=dispatcher.id,
        decided_at=utcnow(),
    )
    db.session.add(recommendation)
    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=dispatcher.id,
            entity_type='AIRecommendation',
            entity_id=recommendation.id,
            action='RESPONSE_DECISION_RECORDED',
            details=(
                f"Dispatcher {dispatcher.username} chose {choice} for incident "
                f"{incident.control_number}; "
                + (
                    f"suggestion was {assessment['outcome']} ({assessment['score']}/100)."
                    if assessment['outcome'] is not None
                    else 'suggestion was withheld for insufficient information.'
                )
                + (f' Reason: {reason}' if reason else '')
            ),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dispatcher failed to record a response decision')
        flash('Unable to save the response decision. Please try again.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    flash('Response decision recorded.', 'success')
    anchor = '#barangay-contact-log' if choice == 'BARANGAY_SUPPORT' else ''
    return redirect(f'{url_for("eoc.emergency_log_detail", incident_id=incident.id)}{anchor}')


@dispatcher_bp.route('/dispatcher/alerts')
def regional_alerts():
    _require_dispatcher()
    status_filter = request.args.get('status', 'ACTIVE').strip().upper()
    query = Alert.query.filter_by(audience='DISPATCHER')
    if status_filter in {'ACTIVE', 'RESOLVED'}:
        query = query.filter(Alert.status == status_filter)
    else:
        status_filter = 'ALL'
    alerts = query.order_by(Alert.created_at.desc()).all()
    return render_template(
        'pages/dispatcher_alerts.html',
        alerts=alerts,
        status_filter=status_filter,
    )


@dispatcher_bp.route('/dispatcher/alerts/<int:alert_id>/resolve', methods=['POST'])
def resolve_regional_alert(alert_id):
    dispatcher = _require_dispatcher()
    alert = Alert.query.filter_by(
        id=alert_id,
        audience='DISPATCHER',
    ).first_or_404()
    if alert.status != 'RESOLVED':
        alert.status = 'RESOLVED'
        db.session.add(AuditEvent(
            user_id=dispatcher.id,
            entity_type='Alert',
            entity_id=alert.id,
            action='RESOLVED',
            details=f'Regional alert #{alert.id} resolved by {dispatcher.username}.',
        ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Dispatcher failed to resolve a regional alert')
            flash('Unable to resolve the alert. Please try again.', 'error')
            return redirect(url_for('dispatcher.regional_alerts'))
    return redirect(url_for('dispatcher.regional_alerts'))


@dispatcher_bp.route('/dispatcher/emergency-log/new', methods=['POST'])
def create_emergency():
    dispatcher = _require_dispatcher()
    location = request.form.get('location', '').strip()
    description = request.form.get('description', '').strip()
    severity = request.form.get('severity', '').strip()
    barangay_id = request.form.get('barangay_id', type=int)
    patient_count = request.form.get('patient_count', 0, type=int)
    if (
        not location
        or len(location) > 255
        or not description
        or len(description) > 255
        or severity not in {'Low', 'Moderate', 'High', 'Severe'}
        or barangay_id is None
        or patient_count is None
        or patient_count < 0
    ):
        flash('Enter the location and emergency details, select a valid severity and barangay, and use a non-negative patient count.', 'error')
        return redirect(url_for('eoc.emergency_log'))

    barangay = Barangay.query.join(Municipality).filter(
        Barangay.id == barangay_id,
        Municipality.code == '043424',
    ).first()
    if barangay is None:
        flash('Select a barangay within San Pablo City.', 'error')
        return redirect(url_for('eoc.emergency_log'))

    incident = Incident(
        hazard_type='EMERGENCY',
        location=location,
        level=severity,
        message=description[:255],
        patient_count=patient_count,
        reported_by=f'dispatcher:{dispatcher.id}',
        barangay_id=barangay.id,
        municipality_id=barangay.municipality_id,
        status='NEW',
        alert=False,
    )
    db.session.add(incident)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dispatcher failed to log an emergency')
        flash('Unable to log the emergency. Please try again.', 'error')
        return redirect(url_for('eoc.emergency_log'))

    flash(_('Emergency %(control_number)s logged.') % {
        'control_number': incident.control_number,
    }, 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@dispatcher_bp.route('/dispatcher/incidents/<int:incident_id>/dispatch', methods=['POST'])
def dispatch_incident(incident_id):
    dispatcher = _require_dispatcher()
    incident = _get_emergency(incident_id)
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    assignee = request.form.get('assignee', '').strip()
    priority = request.form.get('priority', 'MEDIUM').strip().upper()
    due_text = request.form.get('estimated_completion', '').strip()
    vehicle_identifier = request.form.get('vehicle_identifier', '').strip()
    vehicle_location = request.form.get('vehicle_location', '').strip()
    resource_type = request.form.get('resource_type', '').strip()
    resource_quantity = request.form.get('resource_quantity', 1, type=int)
    resource_notes = request.form.get('resource_notes', '').strip()

    if (
        not title
        or len(title) > 200
        or not description
        or priority not in TASK_PRIORITIES
        or len(vehicle_identifier) > 255
        or len(vehicle_location) > 255
        or len(resource_notes) > 4000
        or resource_quantity is None
        or resource_quantity < 1
        or resource_type not in {'', 'Personnel', 'Medical Assistance & Equipment', 'Vehicles'}
    ):
        flash('A task title, description, and valid priority are required.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    due_at = None
    if due_text:
        try:
            due_at = datetime.fromisoformat(due_text)
        except ValueError:
            flash('Enter a valid estimated completion date and time.', 'error')
            return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    assignee_type, separator, assignee_id_text = assignee.partition(':')
    if not separator or assignee_type not in {'user', 'team'} or not assignee_id_text.isdigit():
        flash('Select one CDRRMO responder or response team.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    assigned_user_id = None
    assigned_team_id = None
    if assignee_type == 'user':
        responder = User.query.filter_by(
            id=int(assignee_id_text),
            role='field_responder',
            is_disabled=False,
        ).first()
        if responder is None:
            flash('The selected responder is not an active CDRRMO field responder.', 'error')
            return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
        assigned_user_id = responder.id
    else:
        team = db.get_or_404(ResponseTeam, int(assignee_id_text))
        members = [
            member for member in team.members
            if member.role == 'field_responder'
            and not member.is_disabled
        ]
        if not members:
            flash('Add at least one active CDRRMO responder to this team before dispatching it.', 'error')
            return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
        assigned_team_id = team.id

    response = IncidentResponse.query.filter_by(incident_id=incident.id).first()
    if response and response.status in {'CLOSED', 'RESOLVED'}:
        flash('This emergency response is closed and cannot receive new assignments.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
    if response is None:
        response = IncidentResponse(
            incident_id=incident.id,
            commander_id=None,
            dispatched_by_id=dispatcher.id,
            status='ACTIVE',
            priority_level=priority,
            situation_summary=f'Dispatch initiated for {incident.control_number}.',
        )
        db.session.add(response)
        incident.status = 'ACTIVE'
        incident.alert = True

    task = Task(
        incident_response=response,
        assigned_to_user_id=assigned_user_id,
        assigned_to_team_id=assigned_team_id,
        assigned_by_id=dispatcher.id,
        title=title,
        description=description,
        priority=priority,
        status='PENDING',
        estimated_completion=due_at,
    )
    db.session.add(task)
    if resource_type or vehicle_identifier:
        resource_type = resource_type or 'Vehicles'
        notes = resource_notes or None
        if vehicle_identifier:
            notes = f'{notes}\nVehicle identifier: {vehicle_identifier}' if notes else vehicle_identifier
        db.session.add(Resource(
            incident_response=response,
            resource_type=resource_type,
            quantity=resource_quantity if resource_type != 'Vehicles' or not vehicle_identifier else 1,
            status='DEPLOYED',
            deployed_at=utcnow(),
            location=vehicle_location or None,
            notes=notes,
        ))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dispatcher failed to assign emergency resources')
        flash('Unable to save the dispatch. Please try again.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    flash(_('Task "%(title)s" dispatched to %(assignee)s.') % {
        'title': title,
        'assignee': responder.full_name or responder.username if assigned_user_id else team.name,
    }, 'success')
    return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))


@dispatcher_bp.route('/dispatcher/teams', methods=['GET'])
def response_teams():
    _require_dispatcher_or_imt()
    return render_template(
        'pages/dispatcher_teams.html',
        teams=ResponseTeam.query.order_by(ResponseTeam.name).all(),
        responders=_cdrrmo_responders(),
    )


@dispatcher_bp.route('/dispatcher/teams', methods=['POST'])
def save_response_team():
    editor = _require_dispatcher_or_imt()
    name = request.form.get('name', '').strip()
    team_id = request.form.get('team_id', type=int)
    raw_member_ids = request.form.getlist('member_ids')
    if any(not member_id.isdigit() for member_id in raw_member_ids):
        flash('Choose valid CDRRMO responder entries.', 'error')
        return redirect(url_for('dispatcher.response_teams'))
    member_ids = {int(member_id) for member_id in raw_member_ids}
    if not name or len(name) > 120:
        flash('Team name is required and must be 120 characters or fewer.', 'error')
        return redirect(url_for('dispatcher.response_teams'))

    responders = _cdrrmo_responders()
    valid_responders = {responder.id: responder for responder in responders}
    if not member_ids.issubset(valid_responders):
        flash('Teams may contain only active CDRRMO field responders.', 'error')
        return redirect(url_for('dispatcher.response_teams'))

    duplicate = ResponseTeam.query.filter(
        db.func.lower(ResponseTeam.name) == name.lower()
    ).first()
    if duplicate and duplicate.id != team_id:
        flash('A response team with that name already exists.', 'error')
        return redirect(url_for('dispatcher.response_teams'))

    team = db.get_or_404(ResponseTeam, team_id) if team_id else ResponseTeam(created_by_id=editor.id)
    team.name = name
    team.members = [valid_responders[user_id] for user_id in member_ids]
    db.session.add(team)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dispatcher failed to save a response team')
        flash('Unable to save the response team. Please try again.', 'error')
        return redirect(url_for('dispatcher.response_teams'))
    flash(_('Response team "%(name)s" saved.') % {'name': name}, 'success')
    return redirect(url_for('dispatcher.response_teams'))


@dispatcher_bp.route('/dispatcher/barangay-units')
def barangay_units():
    _require_dispatcher_or_imt()
    units = BarangayResponseUnit.query.join(Barangay).join(Municipality).filter(
        Municipality.code == '043424'
    ).order_by(Barangay.name).all()
    selected_id = request.args.get('unit', type=int)
    selected_unit = next(
        (unit for unit in units if unit.id == selected_id),
        units[0] if units else None,
    )
    recent_contact_logs = []
    phone_call_uri = None
    if selected_unit:
        recent_contact_logs = BarangayContactLog.query.options(
            joinedload(BarangayContactLog.incident)
        ).filter_by(
            response_unit_id=selected_unit.id
        ).order_by(
            BarangayContactLog.contacted_at.desc(),
            BarangayContactLog.id.desc(),
        ).limit(5).all()
        contact_number = (selected_unit.contact_number or '').strip()
        phone_digits = ''.join(
            character for character in contact_number if character in '0123456789'
        )
        if phone_digits:
            phone_call_uri = f"{'+' if contact_number.startswith('+') else ''}{phone_digits}"
    unit_filter = request.args.get('filter', 'all')
    if unit_filter not in {'all', 'needs-info', 'recent'}:
        unit_filter = 'all'
    return render_template(
        'pages/dispatcher_barangay_units.html',
        units=units,
        selected_unit=selected_unit,
        recent_contact_logs=recent_contact_logs,
        phone_call_uri=phone_call_uri,
        search_query=request.args.get('q', '').strip(),
        unit_filter=unit_filter,
        show_mobile_detail=selected_id is not None and selected_unit is not None,
    )


@dispatcher_bp.route('/dispatcher/barangay-units/<int:unit_id>', methods=['POST'])
def update_barangay_unit(unit_id):
    editor = _require_dispatcher_or_imt()
    unit = BarangayResponseUnit.query.join(BarangayResponseUnit.barangay).join(
        Barangay.municipality
    ).filter(
        BarangayResponseUnit.id == unit_id,
        Municipality.code == '043424',
    ).first_or_404()
    contact_person = request.form.get('contact_person', '').strip()
    contact_number = request.form.get('contact_number', '').strip()
    equipment = request.form.get('equipment', '').strip()
    location = request.form.get('location', '').strip()
    team_size_text = request.form.get('team_size', '').strip()
    team_size = None
    if team_size_text:
        try:
            team_size = int(team_size_text)
        except ValueError:
            flash('Team size must be a non-negative whole number.', 'error')
            return redirect(url_for(
                'dispatcher.barangay_units',
                unit=unit.id,
                q=request.form.get('q', '').strip(),
                filter=request.form.get('filter', 'all'),
            ))
        if team_size < 0:
            flash('Team size must be a non-negative whole number.', 'error')
            return redirect(url_for(
                'dispatcher.barangay_units',
                unit=unit.id,
                q=request.form.get('q', '').strip(),
                filter=request.form.get('filter', 'all'),
            ))
    if (
        len(contact_person) > 150
        or len(contact_number) > 30
        or len(equipment) > 4000
        or len(location) > 255
    ):
        flash('One or more directory values exceed their allowed length.', 'error')
        return redirect(url_for(
            'dispatcher.barangay_units',
            unit=unit.id,
            q=request.form.get('q', '').strip(),
            filter=request.form.get('filter', 'all'),
        ))

    unit.contact_person = contact_person or None
    unit.contact_number = contact_number or None
    unit.team_size = team_size
    unit.equipment = equipment or None
    unit.location = location or None
    unit.updated_by_id = editor.id
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Failed to update a barangay response unit')
        flash('Unable to update the barangay response unit. Please try again.', 'error')
        return redirect(url_for(
            'dispatcher.barangay_units',
            unit=unit.id,
            q=request.form.get('q', '').strip(),
            filter=request.form.get('filter', 'all'),
        ))
    flash('Barangay response unit updated.', 'success')
    return redirect(url_for(
        'dispatcher.barangay_units',
        unit=unit.id,
        q=request.form.get('q', '').strip(),
        filter=request.form.get('filter', 'all'),
    ))


@dispatcher_bp.route('/dispatcher/incidents/<int:incident_id>/barangay-contacts', methods=['POST'])
def log_barangay_contact(incident_id):
    dispatcher = _require_dispatcher()
    incident = db.get_or_404(Incident, incident_id)
    if not is_assessable_response_incident(incident):
        abort(404)
    unit = BarangayResponseUnit.query.join(BarangayResponseUnit.barangay).join(
        Barangay.municipality
    ).filter(
        BarangayResponseUnit.id == request.form.get('response_unit_id', type=int),
        Municipality.code == '043424',
    ).first_or_404()
    contact_method = request.form.get('contact_method', 'PHONE').strip().upper()
    outcome = request.form.get('outcome', '').strip().upper()
    contacted_person = request.form.get('contacted_person', '').strip() or unit.contact_person
    notes = request.form.get('notes', '').strip()
    if contact_method not in CONTACT_METHODS or outcome not in CONTACT_OUTCOMES:
        flash('Choose a valid contact method and outcome.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
    if len(contacted_person or '') > 150 or len(notes) > 4000:
        flash('Contact name or notes exceed the allowed length.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))

    db.session.add(BarangayContactLog(
        incident_id=incident.id,
        response_unit_id=unit.id,
        contacted_person=contacted_person,
        contact_method=contact_method,
        outcome=outcome,
        notes=notes or None,
        logged_by_id=dispatcher.id,
    ))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Dispatcher failed to save a barangay contact')
        flash('Unable to save the contact log. Please try again.', 'error')
        return redirect(url_for('eoc.emergency_log_detail', incident_id=incident.id))
    flash(_('Contact with %(barangay)s logged.') % {
        'barangay': unit.barangay.name,
    }, 'success')
    return redirect(
        f'{url_for("eoc.emergency_log_detail", incident_id=incident.id)}'
        '#barangay-contact-log'
    )
