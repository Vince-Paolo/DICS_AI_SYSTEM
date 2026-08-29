import os
import secrets
from datetime import timedelta
from io import BytesIO

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from models import Barangay, Municipality, Province, db, User, Incident, CitizenReport, Alert, utcnow
from services.realtime_data import get_earthquake_data

citizen_bp = Blueprint('citizen', __name__)

ALLOWED_PHOTO_EXTENSIONS = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}
ALLOWED_PHOTO_MIME_TYPES = set(ALLOWED_PHOTO_EXTENSIONS.values())
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def _validate_photo_upload(photo_file):
    if not photo_file or not getattr(photo_file, 'filename', None):
        return None

    filename = secure_filename(photo_file.filename)
    if not filename:
        return None

    ext = os.path.splitext(filename)[1].lower()
    expected_mime = ALLOWED_PHOTO_EXTENSIONS.get(ext)
    if not expected_mime:
        return None

    mimetype = (photo_file.mimetype or '').lower()
    if mimetype not in ALLOWED_PHOTO_MIME_TYPES or mimetype != expected_mime:
        return None

    max_bytes = current_app.config.get('MAX_UPLOAD_SIZE_BYTES')
    if max_bytes is None:
        max_bytes = current_app.config.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024)

    image_bytes = photo_file.read()
    if not image_bytes:
        return None
    if len(image_bytes) > max_bytes:
        return None

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            actual_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    if actual_format not in ALLOWED_IMAGE_FORMATS:
        return None

    return image_bytes, filename, ext


def _is_safe_upload_directory(upload_dir):
    if not upload_dir:
        return False

    abs_upload_dir = os.path.abspath(upload_dir)
    static_dir = os.path.abspath(current_app.static_folder) if current_app.static_folder else None
    if static_dir and os.path.commonpath([abs_upload_dir, static_dir]) == static_dir:
        return False

    return True


@citizen_bp.route('/api/municipalities/<int:province_id>')
def api_municipalities_for_province(province_id):
    """Powers the cascading Province -> Municipality -> Barangay dropdown on
    the citizen report form. Returns only the municipalities belonging to
    the given province, fetched on demand rather than shipping all 142
    CALABARZON municipalities (and, one level down, all 4,018 barangays) in
    every page load."""
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    municipalities = Municipality.query.filter_by(province_id=province_id).order_by(Municipality.name).all()
    return {'municipalities': [{'id': m.id, 'name': m.name} for m in municipalities]}


@citizen_bp.route('/api/barangays/<int:municipality_id>')
def api_barangays_for_municipality(municipality_id):
    if 'username' not in session:
        return {'error': 'Unauthorized'}, 401
    barangays = Barangay.query.filter_by(municipality_id=municipality_id).order_by(Barangay.name).all()
    return {'barangays': [{'id': b.id, 'name': b.name} for b in barangays]}


@citizen_bp.route('/citizen-report', methods=['GET', 'POST'])
def citizen_report():
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    provinces = Province.query.order_by(Province.name).all()

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
        province_id = request.form.get('province_id', type=int)
        municipality_id = request.form.get('municipality_id', type=int)
        barangay_id = request.form.get('barangay_id', type=int)

        # The cascading dropdown only lets a citizen pick a barangay that
        # actually belongs to the selected municipality/province in the UI,
        # but that's client-side JS -- a direct POST (malformed request, or
        # a stale page after the geography data changes) could still submit
        # a mismatched combination. Reject rather than silently store a
        # location that points at inconsistent province/municipality/
        # barangay rows.
        if municipality_id:
            municipality = Municipality.query.get(municipality_id)
            if not municipality or (province_id and municipality.province_id != province_id):
                flash('Selected municipality does not match the selected province.', 'error')
                return redirect(url_for('citizen.citizen_report'))
        if barangay_id:
            barangay = Barangay.query.get(barangay_id)
            if not barangay or (municipality_id and barangay.municipality_id != municipality_id):
                flash('Selected barangay does not match the selected municipality.', 'error')
                return redirect(url_for('citizen.citizen_report'))

        photo_filename = None
        photo_file = request.files.get('photo')
        if photo_file and photo_file.filename:
            validated_photo = _validate_photo_upload(photo_file)
            if not validated_photo:
                flash('Photo upload was invalid.', 'error')
                return redirect(url_for('citizen.citizen_report'))

            image_bytes, _, ext = validated_photo
            upload_dir = current_app.config['UPLOAD_FOLDER']
            if not _is_safe_upload_directory(upload_dir):
                flash('Photo upload was invalid.', 'error')
                return redirect(url_for('citizen.citizen_report'))

            os.makedirs(upload_dir, exist_ok=True)
            stored_name = f"{secrets.token_hex(8)}{ext}"
            photo_path = os.path.join(upload_dir, stored_name)
            with open(photo_path, 'wb') as output_file:
                output_file.write(image_bytes)
            photo_filename = stored_name

        try:
            gps_latitude_value = float(gps_latitude) if gps_latitude else None
            gps_longitude_value = float(gps_longitude) if gps_longitude else None
        except ValueError:
            gps_latitude_value = None
            gps_longitude_value = None

        try:
            twenty_minutes_ago = utcnow() - timedelta(minutes=20)
            duplicate_query = Incident.query.filter(
                Incident.hazard_type == hazard_type,
                Incident.created_at >= twenty_minutes_ago,
                Incident.reported_by == 'citizen',
            )
            if barangay_id:
                duplicate_query = duplicate_query.filter(Incident.barangay_id == barangay_id)
            else:
                duplicate_query = duplicate_query.filter(Incident.location == location)

            duplicate_incident = duplicate_query.order_by(Incident.created_at.desc()).first()

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
                province_id=province_id,
                municipality_id=municipality_id,
                barangay_id=barangay_id,
                anonymous=anonymous,
                photo_filename=photo_filename,
            )
            db.session.add(citizen_report)

            if duplicate_incident:
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    flash(str(e), 'error')
                    return redirect(url_for('citizen.citizen_report'))
                flash(
                    'A similar report for this barangay was submitted recently. Your report has been recorded, but no duplicate incident was created.',
                    'info'
                )
                return redirect(url_for('citizen.citizen_status'))

            db.session.flush()
            incident = Incident(
                user_id=user.id,
                hazard_type=hazard_type,
                location=location,
                message=description,
                level=severity,
                alert=False,
                status='NEW',
                reported_by='citizen',
                province_id=province_id,
                municipality_id=municipality_id,
                barangay_id=barangay_id,
                latitude=gps_latitude_value,
                longitude=gps_longitude_value,
                citizen_report_id=citizen_report.id,
            )
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

    return render_template(
        'pages/citizen_report.html',
        total_incidents=total_incidents,
        pending_count=pending_count,
        provinces=provinces,
    )


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
    # Alerts in the area, not just this citizen's own reports - matches the
    # sidebar notification badge and /citizen-alerts, both of which count
    # all active alerts system-wide.
    alert_count = Incident.query.filter_by(alert=True).count()

    earthquake_data = get_earthquake_data()
    latest_earthquake_magnitude = 0
    if earthquake_data and len(earthquake_data) > 0:
        latest_earthquake_magnitude = earthquake_data[0].get('magnitude', 0)

    return render_template(
        'pages/citizen_dashboard.html',
        username=user.username,
        total_incidents=total_incidents,
        pending_count=pending_count,
        alert_count=alert_count,
        incidents=incidents[:5],
        latest_earthquake_magnitude=latest_earthquake_magnitude,
    )


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

    # Official, human-issued advisories -- distinct from the raw AI risk flags
    # above. See eoc.issue_alert / models.Alert.
    official_alerts = Alert.query.filter_by(status='ACTIVE').order_by(Alert.created_at.desc()).all()

    return render_template('pages/citizen_alerts.html', alerts=alerts, alert_count=alert_count,
                            official_alerts=official_alerts)


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


@citizen_bp.route('/citizen-report/<int:incident_id>')
def citizen_report_detail(incident_id):
    """Citizen can view full details of their own submitted report."""
    if 'username' not in session:
        return redirect(url_for('login'))

    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return redirect(url_for('logout'))

    incident = Incident.query.get_or_404(incident_id)

    # Security: citizen can only view their own reports
    if incident.user_id != user.id:
        flash('You can only view your own reports.', 'danger')
        return redirect(url_for('citizen.citizen_status'))

    # Get matching CitizenReport for photo and GPS details
    citizen_rep = incident.citizen_report
    if citizen_rep is None:
        citizen_rep = CitizenReport.query.filter_by(
            user_id=user.id,
            location=incident.location
        ).order_by(CitizenReport.created_at.desc()).first()

    return render_template(
        'pages/citizen_report_detail.html',
        incident=incident,
        citizen_rep=citizen_rep
    )


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
