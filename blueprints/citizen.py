import os
import secrets

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from models import db, User, Incident, CitizenReport

citizen_bp = Blueprint('citizen', __name__)


@citizen_bp.route('/citizen-report', methods=['GET', 'POST'])
def citizen_report():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    if request.method == 'POST':
        hazard_type = request.form.get('hazard_type', '').strip()
        severity = request.form.get('severity', '').strip()
        location = request.form.get('location', '').strip()
        description = request.form.get('description', '').strip()
        affected_people = request.form.get('affected_people', '0').strip() or '0'
        injuries = request.form.get('injuries', '0').strip() or '0'
        contact = request.form.get('contact', '').strip()
        anonymous = request.form.get('anonymous') == 'on'
        gps_latitude = request.form.get('gps_lat', '').strip()
        gps_longitude = request.form.get('gps_lng', '').strip()

        photo_filename = None
        photo_file = request.files.get('photo')
        if photo_file and photo_file.filename:
            upload_dir = current_app.config['UPLOAD_FOLDER']
            os.makedirs(upload_dir, exist_ok=True)
            filename = secure_filename(photo_file.filename)
            if filename:
                stored_name = f"{secrets.token_hex(8)}_{filename}"
                photo_path = os.path.join(upload_dir, stored_name)
                photo_file.save(photo_path)
                photo_filename = stored_name
            else:
                flash('Photo upload was invalid.', 'error')
                return redirect(url_for('citizen.citizen_report'))

        try:
            gps_latitude_value = float(gps_latitude) if gps_latitude else None
            gps_longitude_value = float(gps_longitude) if gps_longitude else None
        except ValueError:
            gps_latitude_value = None
            gps_longitude_value = None

        try:
            citizen_report = CitizenReport(
                user_id=user.id,
                hazard_type=hazard_type,
                severity=severity,
                location=location,
                description=description,
                affected_people=int(affected_people) if affected_people.isdigit() else None,
                injuries=int(injuries) if injuries.isdigit() else None,
                contact=contact,
                gps_latitude=gps_latitude_value,
                gps_longitude=gps_longitude_value,
                anonymous=anonymous,
                photo_filename=photo_filename,
            )
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                location=location,
                message=description,
                level=severity,
                alert=False,
                status='NEW',
                reported_by='citizen',
            )
            db.session.add(citizen_report)
            db.session.add(incident)
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(str(e), 'error')
                return redirect(url_for('citizen.citizen_report'))
            flash('Incident report submitted successfully. Authorities have been notified.', 'success')
            return redirect(url_for('citizen.citizen_status'))
        except Exception as e:
            flash(f'Error submitting report: {str(e)}', 'error')

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    total_incidents = len(incidents)
    pending_count = sum(1 for i in incidents if not i.alert)

    return render_template('pages/citizen_report.html', total_incidents=total_incidents, pending_count=pending_count)


@citizen_bp.route('/citizen-dashboard')
def citizen_dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    total_incidents = len(incidents)
    pending_count = sum(1 for i in incidents if not i.alert)
    alert_count = sum(1 for i in incidents if i.alert)

    return render_template('pages/citizen_dashboard.html', total_incidents=total_incidents, pending_count=pending_count, alert_count=alert_count, incidents=incidents[:5])


@citizen_bp.route('/citizen-alerts')
def citizen_alerts():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    # Show all public alerts (system-generated + admin-flagged) not just the user's own reports
    alerts = Incident.query.filter(Incident.alert == True).order_by(Incident.created_at.desc()).all()
    alert_count = len(alerts)

    return render_template('pages/citizen_alerts.html', alerts=alerts, alert_count=alert_count)


@citizen_bp.route('/citizen-status')
def citizen_status():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    total_incidents = len(incidents)
    pending_count = sum(1 for i in incidents if not i.alert)

    return render_template('pages/citizen_status.html', incidents=incidents, total_incidents=total_incidents, pending_count=pending_count)


@citizen_bp.route('/citizen-resources')
def citizen_resources():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('pages/citizen_resources.html')


@citizen_bp.route('/incidents')
def incident_history():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incidents = Incident.query.filter_by(user_id=user.id).order_by(Incident.created_at.desc()).all()
    return render_template('pages/incidents.html', incidents=incidents)


@citizen_bp.route('/alerts')
def alerts():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    alerts = Incident.query.filter_by(user_id=user.id, alert=True).order_by(Incident.created_at.desc()).all()
    return render_template('pages/alerts.html', alerts=alerts)


@citizen_bp.route('/emergency-sos', methods=['POST'])
def emergency_sos():
    """
    Emergency SOS endpoint for citizens.
    Creates a high-priority EMERGENCY incident with immediate alert.
    """
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return jsonify({'success': False, 'message': 'User not found'}), 404
    
    # Get location from request if available, otherwise use generic
    location = request.json.get('location', 'User Emergency Location') if request.is_json else 'User Emergency Location'
    
    try:
        emergency_incident = Incident(
            user_id=user.id,
            hazard_type='EMERGENCY',
            location=location,
            message='EMERGENCY SOS Alert from citizen',
            level='CRITICAL',
            alert=True,  # Immediately create alert
            status='NEW',
            reported_by='citizen',
        )
        db.session.add(emergency_incident)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Emergency alert sent! Authorities have been notified immediately.',
            'incident_id': emergency_incident.id
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error creating emergency alert: {str(e)}'}), 500
