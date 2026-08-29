from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy.orm.exc import StaleDataError

from models import (
    AuditEvent,
    Barangay,
    EvacuationCenter,
    Facility,
    Municipality,
    Province,
    User,
    db,
)
from services import permissions as permission_service

facilities_bp = Blueprint('facilities', __name__)

FACILITY_TYPES = [
    'Hospital',
    'Fire Station',
    'Police Station',
    'Evacuation Center',
    'Government Building',
    'School',
    'Bridge',
    'Other',
]


def _current_user():
    return User.query.filter_by(username=session.get('username')).first()


def _log_audit(user, entity_type, entity_id, action, details):
    db.session.add(AuditEvent(
        user_id=user.id if user else None,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        details=details,
    ))


@facilities_bp.route('/facilities')
def list_facilities():
    """Reference directory of critical infrastructure. Read access for EOC/
    Commander/Coordinator; Admin is intentionally excluded (pure
    administration, not incident operations); only EOC can add new entries
    (see can_manage_facilities)."""
    if 'username' not in session:
        return redirect(url_for('login'))
    if not permission_service.has_any_role('EOC', 'COMMANDER', 'COORDINATOR'):
        flash('You do not have permission to view the facility directory.', 'danger')
        return redirect(url_for('dashboard'))

    facility_type_filter = request.args.get('type', '').strip()
    query = Facility.query
    if facility_type_filter:
        query = query.filter(Facility.facility_type == facility_type_filter)
    facilities = query.order_by(Facility.name).all()

    provinces = Province.query.order_by(Province.name).all()

    return render_template(
        'pages/facilities.html',
        facilities=facilities,
        facility_types=FACILITY_TYPES,
        facility_type_filter=facility_type_filter,
        provinces=provinces,
        can_manage=permission_service.can_manage_facilities(_current_user()),
    )


@facilities_bp.route('/facilities/add', methods=['POST'])
def add_facility():
    user = _current_user()
    if not permission_service.can_manage_facilities(user):
        flash('Only EOC staff can add facilities.', 'danger')
        return redirect(url_for('facilities.list_facilities'))

    name = request.form.get('name', '').strip()
    facility_type = request.form.get('facility_type', '').strip()
    address = request.form.get('address', '').strip()
    province_id = request.form.get('province_id', type=int)
    municipality_id = request.form.get('municipality_id', type=int)
    barangay_id = request.form.get('barangay_id', type=int)
    latitude = request.form.get('latitude', type=float)
    longitude = request.form.get('longitude', type=float)
    capacity = request.form.get('capacity', type=int) if facility_type == 'Evacuation Center' else None

    if not name or not facility_type:
        flash('Facility name and type are required.', 'error')
        return redirect(url_for('facilities.list_facilities'))
    if facility_type == 'Evacuation Center' and capacity is not None and capacity < 0:
        flash('Evacuation center capacity cannot be negative.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    if barangay_id:
        barangay = db.session.get(Barangay, barangay_id)
        if not barangay or (municipality_id and barangay.municipality_id != municipality_id):
            flash('Please select a barangay within the selected municipality.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        municipality_id = barangay.municipality_id
    if municipality_id:
        municipality = db.session.get(Municipality, municipality_id)
        if not municipality or (province_id and municipality.province_id != province_id):
            flash('Please select a municipality within the selected province.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        province_id = municipality.province_id
    if province_id and not db.session.get(Province, province_id):
        flash('Please select a valid province.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    facility = Facility(
        name=name,
        facility_type=facility_type,
        address=address or None,
        province_id=province_id,
        municipality_id=municipality_id,
        barangay_id=barangay_id,
        latitude=latitude,
        longitude=longitude,
    )
    db.session.add(facility)

    try:
        db.session.flush()

        # An "Evacuation Center" facility gets its capacity-tracking row created
        # in the same step, rather than requiring a second form/action.
        if facility_type == 'Evacuation Center':
            evacuation_center = EvacuationCenter(
                facility_id=facility.id,
                capacity=capacity,
                occupancy=0,
                status='OPEN',
            )
            db.session.add(evacuation_center)

        _log_audit(user, 'Facility', facility.id, 'CREATED',
                   f'Facility "{name}" ({facility_type}) added by {user.username}.')
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Facility operation failed')
        flash('Unable to complete the facility operation. Please try again.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    flash(f'Facility "{name}" added.', 'success')
    return redirect(url_for('facilities.list_facilities'))


@facilities_bp.route('/evacuation-centers/<int:center_id>/update', methods=['POST'])
def update_evacuation_center(center_id):
    """Update occupancy/status for an evacuation center -- the day-to-day
    operational action, separate from creating the facility record itself."""
    user = _current_user()
    if not permission_service.can_manage_evacuation_centers(user):
        flash('You do not have permission to update evacuation centers.', 'danger')
        return redirect(url_for('facilities.list_facilities'))

    center = db.get_or_404(EvacuationCenter, center_id)
    occupancy = request.form.get('occupancy', type=int)
    status = request.form.get('status', '').strip().upper()

    if occupancy is not None:
        if occupancy < 0:
            flash('Evacuation center occupancy cannot be negative.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        if center.capacity is not None and occupancy > center.capacity:
            flash(
                f'Occupancy cannot exceed this center\'s capacity of {center.capacity}.',
                'error',
            )
            return redirect(url_for('facilities.list_facilities'))
        center.occupancy = occupancy
    if status in ('OPEN', 'FULL', 'CLOSED'):
        center.status = status

    try:
        db.session.flush()
        _log_audit(
            user, 'EvacuationCenter', center.id, 'UPDATED',
            f'Evacuation center #{center.id} updated to status={center.status}, '
            f'occupancy={center.occupancy}/{center.capacity} by {user.username}.',
        )
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        flash('This evacuation center was updated by another user. Reload and try again.', 'warning')
        return redirect(url_for('facilities.list_facilities'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Evacuation center operation failed')
        flash('Unable to complete the evacuation center operation. Please try again.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    flash('Evacuation center updated.', 'success')
    return redirect(url_for('facilities.list_facilities'))


@facilities_bp.route('/citizen-evacuation-centers')
def citizen_evacuation_centers():
    """Citizen-facing read-only view: where can I go, and is there room."""
    if 'username' not in session:
        return redirect(url_for('login'))

    centers = (
        EvacuationCenter.query
        .join(Facility)
        .order_by(Facility.name)
        .all()
    )
    return render_template('pages/citizen_evacuation_centers.html', centers=centers)
