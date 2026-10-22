
from math import isfinite

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
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
from services.realtime_data import SAN_PABLO_BARANGAYS

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
    """Reference directory of critical infrastructure. Read access for IMT;
    Admin is intentionally excluded (pure administration); only IMT can add entries
    (see can_manage_facilities)."""
    if 'username' not in session:
        return redirect(url_for('login'))
    if not permission_service.has_any_role('IMT'):
        flash('You do not have permission to view the facility directory.', 'danger')
        return redirect(url_for('dashboard'))

    facility_type_filter = request.args.get('type', '').strip()
    query = Facility.query.join(Municipality).filter(Municipality.code == '043424')
    if facility_type_filter:
        query = query.filter(Facility.facility_type == facility_type_filter)
    facilities = query.order_by(Facility.name).all()

    provinces = Province.query.filter_by(name='Laguna').order_by(Province.name).all()
    centroids_by_code = {b['code']: {'lat': b['lat'], 'lng': b['lon']} for b in SAN_PABLO_BARANGAYS}
    barangay_coordinates = {
        str(b.id): centroids_by_code[b.code]
        for b in Barangay.query.join(Municipality).filter(Municipality.code == '043424').all()
        if b.code in centroids_by_code
    }

    return render_template(
        'pages/facilities.html',
        facilities=facilities,
        facility_types=FACILITY_TYPES,
        facility_type_filter=facility_type_filter,
        provinces=provinces,
        barangay_coordinates=barangay_coordinates,
        can_manage=permission_service.can_manage_facilities(_current_user()),
    )


@facilities_bp.route('/facilities/add', methods=['POST'])
def add_facility():
    user = _current_user()
    if not permission_service.can_manage_facilities(user):
        flash('Only IMT staff can add facilities.', 'danger')
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

    if (
        latitude is None or longitude is None
        or not isfinite(latitude) or not isfinite(longitude)
        or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
    ):
        flash('Facility location is required. Select a location on the map or enter a valid latitude and longitude.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    if not name or not facility_type:
        flash('Facility name and type are required.', 'error')
        return redirect(url_for('facilities.list_facilities'))
    if facility_type == 'Evacuation Center' and capacity is not None and capacity < 0:
        flash('Evacuation center capacity cannot be negative.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    if barangay_id:
        barangay = db.session.get(Barangay, barangay_id)
        if not barangay or (
            barangay.municipality_id != municipality_id
            or not Municipality.query.filter_by(id=barangay.municipality_id, code='043424').first()
        ):
            flash('Please select a barangay within the selected municipality.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        municipality_id = barangay.municipality_id
    if municipality_id:
        municipality = db.session.get(Municipality, municipality_id)
        if (
            not municipality
            or municipality.code != '043424'
            or (province_id and municipality.province_id != province_id)
        ):
            flash('Please select a municipality within the selected province.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        province_id = municipality.province_id
    if province_id and (
        not db.session.get(Province, province_id)
        or db.session.get(Province, province_id).name.casefold() != 'laguna'
    ):
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
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Facility operation failed')
        flash('Unable to complete the facility operation. Please try again.', 'error')
        return redirect(url_for('facilities.list_facilities'))

    flash(_('Facility "%(name)s" added.') % {'name': name}, 'success')
    return redirect(url_for('facilities.list_facilities'))


@facilities_bp.route('/evacuation-centers/<int:center_id>/update', methods=['POST'])
def update_evacuation_center(center_id):
    """Update the capacity and public status of an evacuation center.

    Occupancy is maintained by response-linked evacuation records so the
    displayed total always has an operational audit trail.
    """
    user = _current_user()
    if not permission_service.can_manage_evacuation_centers(user):
        flash('You do not have permission to update evacuation centers.', 'danger')
        return redirect(url_for('facilities.list_facilities'))

    center = db.get_or_404(EvacuationCenter, center_id)
    status = request.form.get('status', '').strip().upper()

    if 'capacity' in request.form:
        capacity = request.form.get('capacity', type=int)
        if capacity is None or capacity < 0:
            flash('Enter a valid non-negative whole number for capacity.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        if capacity < (center.occupancy or 0):
            flash('Capacity cannot be lower than the current occupancy.', 'error')
            return redirect(url_for('facilities.list_facilities'))
        center.capacity = capacity

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
    except Exception:
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
