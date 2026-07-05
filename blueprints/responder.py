import os
import secrets
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from models import db, User, IncidentResponse, Task, SituationReport
from blueprints.common import is_field_responder

responder_bp = Blueprint('responder', __name__)


@responder_bp.route('/responder-dashboard')
def responder_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    session['agency'] = user.agency or 'FIELD UNIT'
    my_tasks = Task.query.filter_by(assigned_to_agency=user.agency or '').order_by(Task.created_at.desc()).all()
    my_reports = SituationReport.query.filter_by(reporter_id=user.id).order_by(SituationReport.created_at.desc()).limit(8).all()
    active_responses = IncidentResponse.query.filter_by(status='ACTIVE').order_by(IncidentResponse.started_at.desc()).all()

    pending_count = sum(1 for task in my_tasks if task.status in ['PENDING', 'IN_PROGRESS'])
    completed_count = sum(1 for task in my_tasks if task.status == 'COMPLETED')

    return render_template('pages/field_responder_dashboard.html',
        user=user,
        my_tasks=my_tasks,
        my_reports=my_reports,
        active_responses=active_responses,
        pending_count=pending_count,
        completed_count=completed_count,
    )


@responder_bp.route('/responder-tasks')
def responder_tasks():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    status_filter = request.args.get('status', '').upper()
    query = Task.query.filter_by(assigned_to_agency=user.agency or '')
    if status_filter:
        query = query.filter(Task.status == status_filter)

    tasks = query.order_by(Task.created_at.desc()).all()
    return render_template('pages/field_responder_tasks.html', tasks=tasks, status_filter=status_filter, user=user)


@responder_bp.route('/responder-checklist')
def responder_checklist():
    return redirect(url_for('responder.responder_tasks'))


@responder_bp.route('/responder-report', methods=['GET', 'POST'])
def responder_report():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    active_responses = IncidentResponse.query.filter_by(status='ACTIVE').order_by(IncidentResponse.started_at.desc()).all()

    if request.method == 'POST':
        incident_response_id = request.form.get('incident_response_id', type=int)
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        report_type = request.form.get('report_type', 'UPDATE').strip().upper()
        affected_areas = request.form.get('affected_areas', '').strip()
        casualties = request.form.get('casualties', 0, type=int)
        evacuated = request.form.get('evacuated', 0, type=int)
        gps_lat = request.form.get('gps_lat', '').strip()
        gps_lng = request.form.get('gps_lng', '').strip()

        if not incident_response_id or not title or not content:
            flash('Please complete the required fields before submitting your report.', 'danger')
            return redirect(url_for('responder.responder_report'))

        if gps_lat and gps_lng:
            content = f"{content}\nGPS: {gps_lat}, {gps_lng}"

        report = SituationReport(
            incident_response_id=incident_response_id,
            reporter_id=user.id,
            title=title,
            content=content,
            report_type=report_type,
            affected_areas=affected_areas or None,
            casualties=casualties,
            evacuated=evacuated,
        )
        db.session.add(report)
        db.session.commit()

        upload_dir = current_app.config['UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)
        uploaded_files = request.files.getlist('media')
        saved_files = []
        for media_file in uploaded_files:
            if media_file and media_file.filename:
                filename = secure_filename(media_file.filename)
                if filename:
                    saved_name = f"{user.id}_{secrets.token_hex(6)}_{filename}"
                    media_file.save(os.path.join(upload_dir, saved_name))
                    saved_files.append(saved_name)

        if saved_files:
            report.content = f"{report.content}\nAttachments: {', '.join(saved_files)}"
            db.session.commit()

        flash('Field report submitted successfully.', 'success')
        return redirect(url_for('responder.responder_dashboard'))

    return render_template('pages/field_responder_report.html', active_responses=active_responses)


@responder_bp.route('/responder-task/<int:task_id>/update', methods=['POST'])
def responder_update_task(task_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user or user.role != 'field_responder':
        flash('Field responder access required.', 'danger')
        return redirect(url_for('dashboard'))

    task = Task.query.get_or_404(task_id)
    if task.assigned_to_agency != (user.agency or ''):
        flash('You can only update tasks assigned to your unit.', 'danger')
        return redirect(url_for('responder.responder_tasks'))

    new_status = request.form.get('status', '').upper()
    if new_status in {'PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'}:
        task.status = new_status
        if new_status == 'COMPLETED':
            task.completed_at = datetime.utcnow()
        else:
            task.completed_at = None
        db.session.commit()
        flash('Task status updated.', 'success')
    else:
        flash('A valid task status was not provided.', 'danger')

    return redirect(url_for('responder.responder_tasks'))


@responder_bp.route('/responder-task/<int:task_id>/complete', methods=['POST'])
def responder_complete_task(task_id):
    return responder_update_task(task_id)
