from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from models import db, User, Incident, IncidentResponse, Task, Resource, IncidentMessage, PostIncidentReport, Agency
from blueprints.common import is_incident_commander

commander_bp = Blueprint('commander', __name__)


@commander_bp.route('/incident-commander-dashboard')
def incident_commander_dashboard():
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()

    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).all()

    critical_incidents = db.session.query(Incident).filter(
        Incident.level.in_(['CRITICAL', 'HIGH'])
    ).order_by(Incident.created_at.desc()).all()

    total_active = len(active_responses)
    total_critical = len(critical_incidents)
    total_tasks = db.session.query(Task).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Task.status != 'COMPLETED'
    ).count()
    total_resources = db.session.query(Resource).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Resource.status == 'DEPLOYED'
    ).count()

    return render_template('pages/incident_commander_dashboard.html',
                         active_responses=active_responses,
                         critical_incidents=critical_incidents,
                         total_active=total_active,
                         total_critical=total_critical,
                         total_tasks=total_tasks,
                         total_resources=total_resources)


@commander_bp.route('/incident/<int:incident_id>/activate-response', methods=['POST'])
def activate_incident_response(incident_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403

    incident = Incident.query.get_or_404(incident_id)
    commander = User.query.filter_by(username=session['username']).first()

    existing = IncidentResponse.query.filter_by(incident_id=incident_id).first()
    if existing:
        return jsonify({'error': 'Incident response already active'}), 400

    response = IncidentResponse(
        incident_id=incident_id,
        commander_id=commander.id,
        status='ACTIVE',
        situation_summary=f"Incident Response initiated for {incident.hazard_type} at {incident.location}",
        priority_level='CRITICAL' if incident.level == 'CRITICAL' else 'HIGH' if incident.level == 'HIGH' else 'MEDIUM'
    )

    db.session.add(response)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('commander.incident_commander_dashboard'))

    flash(f'Incident response activated for incident {incident_id}', 'success')
    return redirect(url_for('commander.incident_commander_dashboard'))


def compile_incident_timeline(response):
    events = []
    events.append({
        'timestamp': response.started_at,
        'title': 'Response Initiated',
        'type': 'system',
        'icon': 'bi-play-fill',
        'badge_class': 'bg-success',
        'details': 'Commander initiated emergency operations.'
    })

    for task in response.tasks:
        events.append({
            'timestamp': task.created_at,
            'title': f"Task Assigned: {task.title}",
            'type': 'task_assign',
            'icon': 'bi-list-task',
            'badge_class': 'bg-primary',
            'details': f"Assigned to {task.assigned_to_agency} (Priority: {task.priority})."
        })
        if task.completed_at:
            events.append({
                'timestamp': task.completed_at,
                'title': f"Task Completed: {task.title}",
                'type': 'task_complete',
                'icon': 'bi-check-circle-fill',
                'badge_class': 'bg-success',
                'details': f"Completed by {task.assigned_to_agency}."
            })

    for resource in response.resources:
        events.append({
            'timestamp': resource.allocated_at,
            'title': f"Resource Allocated: {resource.quantity}x {resource.resource_type}",
            'type': 'resource_allocate',
            'icon': 'bi-boxes',
            'badge_class': 'bg-info',
            'details': f"Allocated from {resource.agency}."
        })
        if resource.deployed_at:
            events.append({
                'timestamp': resource.deployed_at,
                'title': f"Resource Deployed: {resource.resource_type}",
                'type': 'resource_deploy',
                'icon': 'bi-truck',
                'badge_class': 'bg-success',
                'details': f"Deployed to {resource.location or 'assigned sectors'}."
            })

    for report in response.messages:
        if report.report_type == 'CLOSURE':
            continue
        events.append({
            'timestamp': report.created_at,
            'title': f"Situation Report: {report.title}",
            'type': 'report',
            'icon': 'bi-file-text',
            'badge_class': 'bg-warning text-dark' if report.report_type == 'UPDATE' else 'bg-danger' if report.report_type == 'ALERT' else 'bg-success',
            'details': f"{report.content} (Reporter: {report.reporter.username})"
        })

    if response.closed_at:
        events.append({
            'timestamp': response.closed_at,
            'title': 'Response Closed',
            'type': 'closure',
            'icon': 'bi-archive-fill',
            'badge_class': 'bg-secondary',
            'details': 'Response closed and operations archived.'
        })

    events.sort(key=lambda x: x['timestamp'])
    return events


@commander_bp.route('/incident-response/<int:response_id>/post-incident-evaluation', methods=['POST'])
def post_incident_evaluation(response_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)

    lessons_learned = (request.form.get('lessons_learned') or '').strip()
    response_rating = request.form.get('response_rating', '').strip()
    recommendations = (request.form.get('recommendations') or '').strip()

    report = PostIncidentReport.query.filter_by(incident_response_id=response_id).first()
    if report is None:
        report = PostIncidentReport(incident_response_id=response_id)
        db.session.add(report)

    rating_value = None
    if response_rating.isdigit():
        r_int = int(response_rating)
        if 1 <= r_int <= 5:
            rating_value = r_int
        else:
            flash('Response rating must be between 1 and 5.', 'error')
            return redirect(url_for('commander.incident_response_detail', response_id=response_id))

    report.lessons_learned = lessons_learned or None
    report.response_rating = rating_value
    report.recommendations = recommendations or None

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        flash(f'Unable to save evaluation: {exc}', 'error')
        return redirect(url_for('commander.incident_response_detail', response_id=response_id))

    flash('Post-incident evaluation saved.', 'success')
    return redirect(url_for('commander.incident_response_detail', response_id=response_id))


@commander_bp.route('/incident-response/<int:response_id>')
def incident_response_detail(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    incident = response.incident

    tasks = Task.query.filter_by(incident_response_id=response_id).all()
    resources = Resource.query.filter_by(incident_response_id=response_id).all()
    reports = IncidentMessage.query.filter_by(incident_response_id=response_id).order_by(IncidentMessage.created_at.desc()).all()

    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == 'COMPLETED')
    task_completion_pct = int(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    deployed_resources = sum(1 for r in resources if r.status == 'DEPLOYED')

    total_casualties = db.session.query(db.func.sum(IncidentMessage.casualties)).filter(IncidentMessage.incident_response_id == response_id).scalar() or 0
    total_evacuated = db.session.query(db.func.sum(IncidentMessage.evacuated)).filter(IncidentMessage.incident_response_id == response_id).scalar() or 0
    post_incident_report = PostIncidentReport.query.filter_by(incident_response_id=response_id).first()

    return render_template('pages/incident_response_detail.html',
                         response=response,
                         incident=incident,
                         tasks=tasks,
                         resources=resources,
                         reports=reports,
                         active_tab='dashboard',
                         total_tasks=total_tasks,
                         completed_tasks=completed_tasks,
                         task_completion_pct=task_completion_pct,
                         deployed_resources=deployed_resources,
                         total_casualties=total_casualties,
                         total_evacuated=total_evacuated,
                         post_incident_report=post_incident_report)


@commander_bp.route('/incident-response/<int:response_id>/tasks')
def incident_response_tasks(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    tasks = Task.query.filter_by(incident_response_id=response_id).all()

    return render_template('pages/incident_response_tasks.html',
                         response=response,
                         tasks=tasks,
                         active_tab='tasks')


@commander_bp.route('/incident-response/<int:response_id>/resources')
def incident_response_resources(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    resources = Resource.query.filter_by(incident_response_id=response_id).all()

    return render_template('pages/incident_response_resources.html',
                         response=response,
                         resources=resources,
                         active_tab='resources')


@commander_bp.route('/incident-response/<int:response_id>/reports')
def incident_response_reports(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    reports = IncidentMessage.query.filter_by(incident_response_id=response_id).order_by(IncidentMessage.created_at.desc()).all()

    return render_template('pages/incident_response_reports.html',
                         response=response,
                         reports=reports,
                         active_tab='reports')


@commander_bp.route('/incident-response/<int:response_id>/timeline')
def incident_response_timeline(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    events = compile_incident_timeline(response)

    return render_template('pages/incident_response_timeline.html',
                         response=response,
                         events=events,
                         active_tab='timeline')


@commander_bp.route('/incident-response/<int:response_id>/close', methods=['GET', 'POST'])
def incident_response_close_page(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)

    if request.method == 'POST':
        summary = request.form.get('notes', '').strip()
        casualties = int(request.form.get('casualties') or 0)
        evacuated = int(request.form.get('evacuated') or 0)

        closure_report = IncidentMessage(
            incident_response_id=response_id,
            reporter_id=commander.id,
            title='Operational Closure Summary',
            content=summary or 'Incident response operations closed by commander.',
            report_type='CLOSURE',
            source='commander',
            casualties=casualties,
            evacuated=evacuated,
            affected_areas='All Areas Closed'
        )

        response.status = 'CLOSED'
        response.closed_at = datetime.utcnow()
        response.resolved_at = datetime.utcnow()
        response.situation_summary = f"Response Closed. Total Casualties: {casualties}, Total Evacuated: {evacuated}"
        response.incident.alert = False

        db.session.add(closure_report)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(url_for('commander.incident_commander_dashboard'))

        flash('Incident response closed successfully and incident marked resolved.', 'success')
        return redirect(url_for('commander.incident_commander_dashboard'))

    return render_template('pages/incident_response_close.html',
                         response=response,
                         active_tab='close')


@commander_bp.route('/incident-response/<int:response_id>/assign-task', methods=['GET', 'POST'])
def assign_task(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)

    agencies = Agency.query.order_by(Agency.name).all()

    if request.method == 'POST':
        agency = request.form.get('agency', '').strip()
        title = request.form.get('title')
        description = request.form.get('description')
        priority = request.form.get('priority', 'MEDIUM')
        estimated_completion = request.form.get('estimated_completion')

        # Validate agency exists in the Agency table
        valid_agency_names = [a.name for a in agencies]
        if agency not in valid_agency_names:
            flash(f'Invalid agency "{agency}". Please select from the list.', 'error')
            return redirect(url_for('incident_response_tasks', response_id=response_id))

        task = Task(
            incident_response_id=response_id,
            assigned_to_agency=agency,
            assigned_by_id=commander.id,
            title=title,
            description=description,
            priority=priority,
            status='PENDING',
            estimated_completion=datetime.fromisoformat(estimated_completion) if estimated_completion else None
        )

        db.session.add(task)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(url_for('commander.incident_response_tasks', response_id=response_id))

        flash(f'Task "{title}" assigned to {agency}', 'success')
        return redirect(url_for('commander.incident_response_tasks', response_id=response_id))

    return render_template('pages/assign_task.html', response=response, agencies=agencies, active_tab='tasks')


@commander_bp.route('/incident-response/<int:response_id>/allocate-resource', methods=['GET', 'POST'])
def allocate_resource(response_id):
    if not is_incident_commander():
        flash('Incident Commander access required.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)

    if request.method == 'POST':
        resource_type = request.form.get('resource_type')
        agency = request.form.get('agency')
        quantity = int(request.form.get('quantity', 1))
        location = request.form.get('location')
        notes = request.form.get('notes')

        if quantity < 1:
            flash('Quantity must be at least 1.', 'error')
            return redirect(url_for('incident_response_resources', response_id=response_id))

        resource = Resource(
            incident_response_id=response_id,
            resource_type=resource_type,
            agency=agency,
            quantity=quantity,
            location=location,
            notes=notes,
            status='AVAILABLE'
        )

        db.session.add(resource)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(url_for('commander.incident_response_resources', response_id=response_id))

        flash(f'Resource allocated: {quantity} x {resource_type} from {agency}', 'success')

    return redirect(url_for('commander.incident_response_resources', response_id=response_id))


@commander_bp.route('/incident-response/<int:response_id>/create-report', methods=['GET', 'POST'])
def create_situation_report(response_id):
    """
    Create a situation report for an incident response.
    
    Only the assigned Incident Commander can create situation reports for their response.
    
    Agency Coordinators use the separate coordinator_submit_report route to submit
    broadcast messages and reports for their agency's participation.
    """
    if not is_incident_commander():
        flash('Only Incident Commanders can create situation reports. Coordinators use their Coordinator portal to submit reports.', 'danger')
        return redirect(url_for('dashboard'))

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        flash('You can only create situation reports for incidents you command.', 'danger')
        return redirect(url_for('commander.incident_commander_dashboard'))
    reporter = commander

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        report_type = request.form.get('report_type', 'UPDATE')
        affected_areas = request.form.get('affected_areas')
        casualties = request.form.get('casualties', type=int) or 0
        evacuated = request.form.get('evacuated', type=int) or 0

        report = IncidentMessage(
            incident_response_id=response.id,
            reporter_id=reporter.id,
            title=title,
            content=content,
            report_type=report_type,
            source='commander',
            affected_areas=affected_areas,
            casualties=casualties,
            evacuated=evacuated
        )

        db.session.add(report)
        response.situation_summary = f"Latest Report: {title}"
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(str(e), 'error')
            return redirect(url_for('commander.incident_response_reports', response_id=response_id))

        flash(f'Situation report "{title}" created successfully', 'success')

    return redirect(url_for('commander.incident_response_reports', response_id=response_id))


@commander_bp.route('/incident-response/<int:response_id>/update-task/<int:task_id>', methods=['POST'])
def update_task(response_id, task_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    task = Task.query.get_or_404(task_id)
    status = request.form.get('status')

    task.status = status
    if status == 'COMPLETED':
        task.completed_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('commander.incident_response_tasks', response_id=response_id))

    flash(f'Task status updated to {status}', 'success')
    return redirect(url_for('commander.incident_response_tasks', response_id=response_id))


@commander_bp.route('/incident-response/<int:response_id>/update-resource/<int:resource_id>', methods=['POST'])
def update_resource(response_id, resource_id):
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403

    commander = User.query.filter_by(username=session['username']).first()
    response = IncidentResponse.query.get_or_404(response_id)
    if response.commander_id != commander.id:
        abort(403)
    resource = Resource.query.get_or_404(resource_id)
    status = request.form.get('status')
    location = request.form.get('location')

    resource.status = status
    if location:
        resource.location = location
    if status == 'DEPLOYED':
        resource.deployed_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(url_for('commander.incident_response_resources', response_id=response_id))

    flash(f'Resource status updated to {status}', 'success')
    return redirect(url_for('commander.incident_response_resources', response_id=response_id))


@commander_bp.route('/api/incident-response-stats')
def get_incident_response_stats():
    if not is_incident_commander():
        return jsonify({'error': 'Unauthorized'}), 403

    commander = User.query.filter_by(username=session['username']).first()

    active_responses = db.session.query(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        IncidentResponse.status.in_(['ACTIVE', 'MONITORING'])
    ).count()

    pending_tasks = db.session.query(Task).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Task.status.in_(['PENDING', 'IN_PROGRESS'])
    ).count()

    deployed_resources = db.session.query(Resource).join(IncidentResponse).filter(
        IncidentResponse.commander_id == commander.id,
        Resource.status == 'DEPLOYED'
    ).count()

    return jsonify({
        'active_responses': active_responses,
        'pending_tasks': pending_tasks,
        'deployed_resources': deployed_resources
    })
