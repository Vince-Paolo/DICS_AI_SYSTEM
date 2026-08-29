from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models import db, User, Incident, IncidentResponse, Task, Resource, IncidentMessage, ResourceRequest, AuditEvent, utcnow
from blueprints.common import (
    can_view_response,
    current_user,
    get_coordinator_agency,
    is_agency_coordinator,
    user_agency_has_response,
)
from services import permissions as permission_service

coordinator_bp = Blueprint('coordinator', __name__)


def _agency_response_ids(agency):
    if not agency:
        return []
    task_response_ids = {t.incident_response_id for t in Task.query.filter_by(assigned_to_agency=agency).all()}
    resource_response_ids = {r.incident_response_id for r in Resource.query.filter_by(agency=agency).all()}
    return list(task_response_ids.union(resource_response_ids))


@coordinator_bp.route('/coordinator')
def coordinator_dashboard():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()

    my_tasks = Task.query.join(IncidentResponse).filter(
        Task.assigned_to_agency == agency,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Task.created_at.desc()).all() if agency else []

    pending_count = sum(1 for t in my_tasks if t.status in ('PENDING', 'IN_PROGRESS'))

    agency_response_ids = _agency_response_ids(agency)
    active_responses = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING']),
        IncidentResponse.id.in_(agency_response_ids)
    ).order_by(IncidentResponse.started_at.desc()).all() if agency_response_ids else []

    deployed_resources = Resource.query.filter_by(status='DEPLOYED', agency=agency).count() if agency else 0
    active_incidents = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING']),
        IncidentResponse.id.in_(agency_response_ids)
    ).count() if agency_response_ids else 0
    total_critical = IncidentResponse.query.filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING']),
        IncidentResponse.priority_level == 'CRITICAL',
        IncidentResponse.id.in_(agency_response_ids)
    ).count() if agency_response_ids else 0

    return render_template('pages/coordinator_dashboard.html',
        my_tasks=my_tasks,
        pending_count=pending_count,
        active_responses=active_responses,
        deployed_resources=deployed_resources,
        active_incidents=active_incidents,
        total_critical=total_critical
    )


@coordinator_bp.route('/coordinator/tasks')
def coordinator_tasks():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    status_filter = request.args.get('status')
    priority_filter = request.args.get('priority')

    query = Task.query.filter_by(assigned_to_agency=agency) if agency else Task.query

    if status_filter:
        query = query.filter(Task.status == status_filter)
    if priority_filter:
        query = query.filter(Task.priority == priority_filter)

    tasks = query.order_by(Task.created_at.desc()).all()
    return render_template('pages/coordinator_tasks.html', tasks=tasks)


@coordinator_bp.route('/coordinator/tasks/<int:task_id>/update', methods=['POST'])
def coordinator_update_task(task_id):
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    task = Task.query.get_or_404(task_id)
    agency = get_coordinator_agency()
    if not agency or task.assigned_to_agency != agency:
        flash('You can only update tasks assigned to your agency.', 'error')
        return redirect(request.referrer or url_for('coordinator.coordinator_tasks'))

    new_status = request.form.get('status')
    referrer = request.referrer

    if new_status in ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'):
        task.status = new_status
        if new_status == 'COMPLETED':
            task.completed_at = utcnow()
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(referrer or url_for('coordinator.coordinator_tasks'))
        flash(f'Task "{task.title}" updated to {new_status}.', 'success')

    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator.coordinator_tasks'))


@coordinator_bp.route('/coordinator/team')
def coordinator_team():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    resources = Resource.query.join(IncidentResponse).filter(
        Resource.agency == agency,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Resource.allocated_at.desc()).all() if agency else []

    team_counts = {}
    for r in resources:
        team_counts[r.agency] = team_counts.get(r.agency, 0) + r.quantity

    all_tasks = Task.query.filter_by(assigned_to_agency=agency).all() if agency else []

    task_summary = {}
    for t in all_tasks:
        agency_name = t.assigned_to_agency
        if agency_name not in task_summary:
            task_summary[agency_name] = {'total': 0, 'completed': 0}
        task_summary[agency_name]['total'] += 1
        if t.status == 'COMPLETED':
            task_summary[agency_name]['completed'] += 1

    return render_template('pages/coordinator_team.html',
        resources=resources,
        team_counts=team_counts,
        task_summary=task_summary
    )


@coordinator_bp.route('/coordinator/resources')
def coordinator_resources():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    resources = Resource.query.join(IncidentResponse).filter(
        Resource.agency == agency,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Resource.allocated_at.desc()).all() if agency else []

    active_response_ids = _agency_response_ids(agency)
    active_responses = IncidentResponse.query.filter(
        IncidentResponse.id.in_(active_response_ids)
    ).order_by(IncidentResponse.started_at.desc()).all() if active_response_ids else []

    deployed_count = sum(1 for r in resources if r.status == 'DEPLOYED')
    available_count = sum(1 for r in resources if r.status == 'AVAILABLE')
    returning_count = sum(1 for r in resources if r.status == 'RETURNING')
    unavail_count = sum(1 for r in resources if r.status == 'UNAVAILABLE')

    return render_template('pages/coordinator_resources.html',
        resources=resources,
        active_responses=active_responses,
        deployed_count=deployed_count,
        available_count=available_count,
        returning_count=returning_count,
        unavail_count=unavail_count
    )


@coordinator_bp.route('/coordinator/resources/allocate', methods=['POST'])
def coordinator_allocate_resource():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response_id = request.form.get('response_id', type=int)
    agency = get_coordinator_agency()
    resource_type = request.form.get('resource_type', '').strip()
    quantity = request.form.get('quantity', type=int, default=1)
    status = request.form.get('status', 'AVAILABLE')
    location = request.form.get('location', '').strip()
    notes = request.form.get('notes', '').strip()

    if not agency:
        flash('Coordinator agency is not configured.', 'error')
        return redirect(url_for('coordinator.coordinator_resources'))

    if not all([response_id, resource_type, quantity]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('coordinator.coordinator_resources'))

    if quantity < 1:
        flash('Quantity must be at least 1.', 'error')
        return redirect(url_for('coordinator.coordinator_resources'))

    response = IncidentResponse.query.get_or_404(response_id)
    user = current_user()
    if not user_agency_has_response(user, response):
        flash('You can only allocate resources to responses that include your agency.', 'error')
        return redirect(url_for('coordinator.coordinator_resources'))

    resource = Resource(
        incident_response_id=response.id,
        resource_type=resource_type,
        agency=agency,
        quantity=quantity,
        status=status,
        location=location or None,
        notes=notes or None,
        deployed_at=utcnow() if status == 'DEPLOYED' else None
    )
    db.session.add(resource)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('coordinator.coordinator_resources'))
    flash(f'{quantity}x {resource_type} ({agency}) allocated to Response #{response.incident_id}.', 'success')
    return redirect(url_for('coordinator.coordinator_resources'))


@coordinator_bp.route('/coordinator/resources/<int:resource_id>/update', methods=['POST'])
def coordinator_update_resource(resource_id):
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    resource = Resource.query.get_or_404(resource_id)
    agency = get_coordinator_agency()
    if not agency or resource.agency != agency:
        flash('You can only update resources allocated to your agency.', 'error')
        return redirect(request.referrer or url_for('coordinator.coordinator_resources'))

    new_status = request.form.get('status')
    referrer = request.referrer

    if new_status in ('AVAILABLE', 'DEPLOYED', 'RETURNING', 'UNAVAILABLE'):
        resource.status = new_status
        if new_status == 'DEPLOYED' and not resource.deployed_at:
            resource.deployed_at = utcnow()
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(referrer or url_for('coordinator.coordinator_resources'))
        flash(f'Resource status updated to {new_status}.', 'success')

    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator.coordinator_resources'))


@coordinator_bp.route('/coordinator/reports')
def coordinator_reports():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    response_ids = _agency_response_ids(agency)
    reports = IncidentMessage.query.filter(
        IncidentMessage.incident_response_id.in_(response_ids)
    ).order_by(IncidentMessage.created_at.desc()).all() if response_ids else []

    active_responses = IncidentResponse.query.filter(
        IncidentResponse.id.in_(response_ids)
    ).order_by(IncidentResponse.started_at.desc()).all() if response_ids else []

    return render_template('pages/coordinator_reports.html',
        reports=reports,
        active_responses=active_responses
    )


@coordinator_bp.route('/coordinator/reports/submit', methods=['POST'])
def coordinator_submit_report():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response_id = request.form.get('response_id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    report_type = request.form.get('report_type', 'UPDATE')
    affected_areas = request.form.get('affected_areas', '').strip()
    evacuated = request.form.get('evacuated', type=int)
    casualties = request.form.get('casualties', type=int)

    if not all([response_id, title, content]):
        flash('Please fill in all required fields.', 'error')
        referrer = request.referrer
        return redirect(referrer or url_for('coordinator.coordinator_reports'))

    user = User.query.filter_by(username=session['username']).first()
    agency = get_coordinator_agency()
    response = IncidentResponse.query.get_or_404(response_id)

    if not agency:
        flash('Coordinator agency is not configured.', 'error')
        return redirect(request.referrer or url_for('coordinator.coordinator_reports'))

    has_owned_assets = any(
        task.assigned_to_agency == agency for task in response.tasks
    ) or any(
        resource.agency == agency for resource in response.resources
    )
    if not has_owned_assets:
        flash('You can only submit reports for responses that include your agency.', 'error')
        return redirect(request.referrer or url_for('coordinator.coordinator_reports'))

    message = IncidentMessage(
        incident_response_id=response.id,
        reporter_id=user.id,
        title=title,
        content=content,
        report_type=report_type if report_type in ('UPDATE', 'ALERT', 'MILESTONE', 'CLOSURE') else 'UPDATE',
        source='coordinator',
        affected_areas=affected_areas or None,
        evacuated=evacuated,
        casualties=casualties
    )
    db.session.add(message)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(referrer or url_for('coordinator.coordinator_reports'))
    flash(f'Broadcast message "{title}" submitted successfully.', 'success')

    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('coordinator.coordinator_reports'))


@coordinator_bp.route('/coordinator/comms')
def coordinator_comms():
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    response_ids = _agency_response_ids(agency)
    comm_logs = IncidentMessage.query.filter(
        IncidentMessage.incident_response_id.in_(response_ids)
    ).order_by(IncidentMessage.created_at.desc()).limit(50).all() if response_ids else []

    active_responses = IncidentResponse.query.filter(
        IncidentResponse.id.in_(response_ids)
    ).order_by(IncidentResponse.started_at.desc()).all() if response_ids else []

    return render_template('pages/coordinator_comms.html',
        comm_logs=comm_logs,
        active_responses=active_responses
    )


@coordinator_bp.route('/coordinator/response/<int:response_id>')
def coordinator_response_detail(response_id):
    if not is_agency_coordinator():
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    response = IncidentResponse.query.get_or_404(response_id)
    user = current_user()
    if not can_view_response(response):
        flash('Access denied.', 'error')
        return redirect(url_for('coordinator.coordinator_dashboard'))

    agency = get_coordinator_agency()
    agency_tasks = [t for t in response.tasks if t.assigned_to_agency == agency] if agency else []

    return render_template('pages/coordinator_response_detail.html',
        response=response,
        agency_tasks=agency_tasks
    )


@coordinator_bp.route('/coordinator/quick-report', methods=['POST'])
def coordinator_quick_report():
    return coordinator_submit_report()


@coordinator_bp.route('/coordinator/resource-requests')
def coordinator_resource_requests():
    """List this agency's resource requests and expose the submission form.

    Distinct from /coordinator/resources (direct allocation of resources the
    coordinator already controls): this is for asking EOC/Commander for
    something the agency doesn't have on hand yet, which needs a decision.
    """
    if not permission_service.can_request_resources(current_user()):
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    requests_ = ResourceRequest.query.filter_by(agency=agency).order_by(
        ResourceRequest.created_at.desc()
    ).all() if agency else []

    active_incidents = Incident.query.join(IncidentResponse).filter(
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).order_by(Incident.created_at.desc()).all()

    return render_template('pages/coordinator_resource_requests.html',
        requests=requests_,
        active_incidents=active_incidents,
    )


@coordinator_bp.route('/coordinator/resource-requests/submit', methods=['POST'])
def coordinator_submit_resource_request():
    user = current_user()
    if not permission_service.can_request_resources(user):
        flash('Access denied.', 'error')
        return redirect(url_for('login'))

    agency = get_coordinator_agency()
    if not agency:
        flash('Coordinator agency is not configured.', 'error')
        return redirect(url_for('coordinator.coordinator_resource_requests'))

    incident_id = request.form.get('incident_id', type=int)
    resource_type = request.form.get('resource_type', '').strip()
    quantity = request.form.get('quantity', type=int)

    if not incident_id or not resource_type or not quantity or quantity < 1:
        flash('Incident, resource type, and a valid quantity are required.', 'error')
        return redirect(url_for('coordinator.coordinator_resource_requests'))

    incident = Incident.query.get_or_404(incident_id)

    resource_request = ResourceRequest(
        incident_id=incident.id,
        resource_type=resource_type,
        quantity=quantity,
        requested_by_id=user.id,
        agency=agency,
        status='OPEN',
    )
    db.session.add(resource_request)
    try:
        db.session.flush()
        db.session.add(AuditEvent(
            user_id=user.id,
            entity_type='ResourceRequest',
            entity_id=resource_request.id,
            action='REQUESTED',
            details=f'{agency} requested {quantity}x {resource_type} for incident #{incident.id}.',
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('coordinator.coordinator_resource_requests'))

    flash(f'Resource request for {quantity}x {resource_type} submitted.', 'success')
    return redirect(url_for('coordinator.coordinator_resource_requests'))
