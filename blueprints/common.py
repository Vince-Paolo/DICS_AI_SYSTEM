from flask import session

from models import User
import services.permissions as permission_service


def current_user():
    if 'username' not in session:
        return None
    return User.query.filter_by(username=session['username']).first()


def has_role(*roles):
    return 'role' in session and session.get('role') in roles


def is_admin():
    """Backward-compatible helper delegating to the shared permission module."""
    return permission_service.is_admin()


def is_agency_coordinator():
    return permission_service.is_coordinator()


def is_incident_commander():
    return permission_service.is_commander()


def is_field_responder():
    return permission_service.is_responder()


def is_eoc_staff():
    return permission_service.is_eoc()


def is_admin_coordinator_or_commander():
    return is_admin() or is_agency_coordinator() or is_incident_commander()


def is_admin_or_eoc():
    """Check if user is admin or EOC staff (for dispatch-style operations:
    verifying incidents, assigning/transferring commanders, toggling alerts)."""
    return is_admin() or is_eoc_staff()


def get_coordinator_agency():
    user = current_user()
    return user.agency if user else None


def user_agency_has_response(user, response):
    if not user or not response:
        return False
    agency = user.agency
    if not agency:
        return False
    return any(task.assigned_to_agency == agency for task in response.tasks) or \
           any(resource.agency == agency for resource in response.resources)


def can_manage_response(response):
    if not response:
        return False
    if is_admin():
        return True
    if is_incident_commander():
        user = current_user()
        return user is not None and response.commander_id == user.id
    return False


def can_view_response(response):
    if not response:
        return False
    if is_admin() or is_eoc_staff():
        return True
    if can_manage_response(response):
        return True
    if is_agency_coordinator():
        user = current_user()
        return user_agency_has_response(user, response)
    return False


def can_manage_agency_data(agency):
    user = current_user()
    if not user or not agency:
        return False
    if is_admin():
        return True
    return (is_agency_coordinator() or is_field_responder()) and user.agency == agency
