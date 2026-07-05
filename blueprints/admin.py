import os
import sqlite3
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for, send_file
from werkzeug.security import generate_password_hash

from models import db, User, Incident, IncidentResponse, Task, Resource, SituationReport
from blueprints.common import is_admin_or_coordinator, is_incident_commander, is_field_responder, is_eoc_staff

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/admin')
def admin():
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    return redirect(url_for('admin.admin_alerts'))


@admin_bp.route('/admin/alerts')
def admin_alerts():
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incidents = Incident.query.order_by(Incident.created_at.desc()).all()
    return render_template('pages/admin_alerts.html', incidents=incidents)


@admin_bp.route('/admin/alerts/<int:incident_id>/toggle', methods=['POST'])
def toggle_alert(incident_id):
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    incident = Incident.query.get_or_404(incident_id)
    incident.alert = not incident.alert
    db.session.commit()
    flash('Alert status updated.', 'success')
    return redirect(url_for('admin.admin_alerts'))


@admin_bp.route('/admin/users')
def manage_users():
    """User management page for admins and coordinators"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    users = User.query.order_by(User.created_at.desc()).all()
    roles = ['user', 'agency_coordinator', 'admin']
    return render_template('pages/user_management.html', users=users, roles=roles)


@admin_bp.route('/admin/users/add', methods=['POST'])
def add_user():
    """Add a new user"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        contact_number = request.form.get('contact_number', '').strip()
        agency = request.form.get('agency', '').strip()
        role = request.form.get('role', 'user')

        if not username or not password or not email:
            flash('Username, email, and password are required.', 'error')
            return redirect(url_for('admin.manage_users'))

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return redirect(url_for('admin.manage_users'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return redirect(url_for('admin.manage_users'))

        new_user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            full_name=full_name,
            contact_number=contact_number,
            agency=agency,
            role=role,
            email_verified=True,
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f'User "{username}" created successfully.', 'success')
    except Exception as e:
        flash(f'Error creating user: {str(e)}', 'error')

    return redirect(url_for('admin.manage_users'))


@admin_bp.route('/admin/users/<int:user_id>/update', methods=['POST'])
def update_user(user_id):
    """Update user details and role"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)

    try:
        if user.role == 'admin' and request.form.get('role') != 'admin':
            admin_count = User.query.filter_by(role='admin').count()
            if admin_count <= 1:
                flash('Cannot remove last admin account.', 'error')
                return redirect(url_for('admin.manage_users'))

        user.full_name = request.form.get('full_name', user.full_name).strip()
        user.contact_number = request.form.get('contact_number', user.contact_number).strip()
        user.agency = request.form.get('agency', user.agency).strip()
        user.role = request.form.get('role', user.role)

        db.session.commit()
        flash(f'User "{user.username}" updated successfully.', 'success')
    except Exception as e:
        flash(f'Error updating user: {str(e)}', 'error')

    return redirect(url_for('admin.manage_users'))


@admin_bp.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
def toggle_user_status(user_id):
    """Disable/enable user account"""
    if not is_admin_or_coordinator():
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)

    try:
        if user.role == 'admin' and not user.is_disabled:
            admin_count = User.query.filter_by(role='admin', is_disabled=False).count()
            if admin_count <= 1:
                flash('Cannot disable last active admin account.', 'error')
                return redirect(url_for('admin.manage_users'))

        user.is_disabled = not user.is_disabled
        db.session.commit()
        status = 'disabled' if user.is_disabled else 'enabled'
        flash(f'User "{user.username}" {status} successfully.', 'success')
    except Exception as e:
        flash(f'Error toggling user status: {str(e)}', 'error')

    return redirect(url_for('admin.manage_users'))


@admin_bp.route('/admin/backup')
def export_backup():
    """Export SQLite database as a downloadable backup file"""
    if not session.get('role') == 'admin':
        flash('Admin access required.', 'danger')
        return redirect(url_for('admin.manage_users'))

    try:
        db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'instance', 'database.db')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'dics_ai_backup_{timestamp}.db'
        backup_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'instance', backup_filename)

        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()

        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_filename,
            mimetype='application/octet-stream'
        )
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'error')
        return redirect(url_for('admin.manage_users'))
