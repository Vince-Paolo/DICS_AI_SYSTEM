from flask import session

from models import User


def is_admin():
    """Check if user is an admin (for admin-only operations like user management, system backups)"""
    return 'username' in session and session.get('role') == 'admin'


def is_admin_or_coordinator():
    """Check if user is admin or coordinator (for viewing/managing agency operations)"""
    return 'username' in session and session.get('role') in ['admin', 'agency_coordinator']


def is_incident_commander():
    return 'username' in session and session.get('role') in ['incident_commander', 'admin']


def is_admin_coordinator_or_commander():
    return 'username' in session and session.get('role') in ['admin', 'agency_coordinator', 'incident_commander']


def is_field_responder():
    return 'username' in session and session.get('role') == 'field_responder'


def is_eoc_staff():
    return 'username' in session and session.get('role') == 'eoc_staff'


def is_admin_or_eoc():
    """Check if user is admin or EOC staff (for dispatch-style operations:
    verifying incidents, assigning/transferring commanders, toggling alerts)"""
    return 'username' in session and session.get('role') in ['admin', 'eoc_staff']


def is_coordinator():
    return 'username' in session and session.get('role') == 'agency_coordinator'


def get_coordinator_agency():
    user = User.query.filter_by(username=session.get('username')).first()
    return user.agency if user else None
