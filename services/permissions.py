from __future__ import annotations

from typing import Iterable

from flask import session

from models import User


ROLE_ALIASES = {
    'citizen': 'CITIZEN',
    'user': 'CITIZEN',
    'field_responder': 'RESPONDER',
    'responder': 'RESPONDER',
    'agency_coordinator': 'COORDINATOR',
    'coordinator': 'COORDINATOR',
    'incident_commander': 'COMMANDER',
    'commander': 'COMMANDER',
    'eoc_staff': 'EOC',
    'eoc': 'EOC',
    'admin': 'ADMIN',
}


def normalize_role(role: str | None) -> str | None:
    if not role:
        return None
    return ROLE_ALIASES.get(str(role).strip().lower(), str(role).strip().upper())


def current_role() -> str | None:
    try:
        return normalize_role(session.get('role'))
    except RuntimeError:
        return None


def current_user():
    try:
        username = session.get('username')
    except RuntimeError:
        return None
    return User.query.filter_by(username=username).first() if username else None


def current_user_for_roles(*roles):
    user = current_user()
    return user if user and user_has_any_role(user, *roles) else None


def is_authenticated() -> bool:
    return current_user() is not None


def has_any_role(*roles: str) -> bool:
    role = current_role()
    if not role:
        return False
    return role in {normalize_role(r) for r in roles}


def user_has_any_role(user, *roles: str) -> bool:
    role = normalize_role(getattr(user, 'role', None))
    if not role:
        return False
    return role in {normalize_role(r) for r in roles}


def is_citizen() -> bool:
    return has_any_role('CITIZEN')


def is_responder() -> bool:
    return has_any_role('RESPONDER')


def is_coordinator() -> bool:
    return has_any_role('COORDINATOR')


def is_commander() -> bool:
    return has_any_role('COMMANDER')


def is_eoc() -> bool:
    return has_any_role('EOC')


def is_admin() -> bool:
    return has_any_role('ADMIN')


def can_view_incident(user, incident) -> bool:
    if not user or not incident:
        return False
    if user_has_any_role(user, 'ADMIN'):
        return True
    if user_has_any_role(user, 'CITIZEN'):
        return getattr(incident, 'user_id', None) == user.id
    if user_has_any_role(user, 'RESPONDER'):
        return True
    if user_has_any_role(user, 'COORDINATOR', 'COMMANDER', 'EOC'):
        return True
    return False


def can_edit_incident(user, incident) -> bool:
    if not user or not incident:
        return False
    if user_has_any_role(user, 'ADMIN'):
        return True
    if user_has_any_role(user, 'CITIZEN'):
        return getattr(incident, 'user_id', None) == user.id
    if user_has_any_role(user, 'COORDINATOR', 'COMMANDER', 'EOC'):
        return True
    return False


def can_assign_task(user, incident) -> bool:
    if not user or not incident:
        return False
    return user_has_any_role(user, 'ADMIN', 'COORDINATOR', 'COMMANDER', 'EOC')


def can_allocate_resource(user, resource) -> bool:
    if not user or not resource:
        return False
    return user_has_any_role(user, 'ADMIN', 'COORDINATOR', 'COMMANDER', 'EOC')


def can_verify_incident(user) -> bool:
    return user_has_any_role(user, 'ADMIN', 'EOC')


def can_issue_alert(user) -> bool:
    """Publishing an official, citizen-facing alert is an EOC/Commander
    operational action. Admin is intentionally excluded -- same boundary
    as the rest of admin's role being pure administration, not incident
    operations (see is_agency_coordinator() in blueprints/common.py for the
    coordinator-dashboard precedent for this)."""
    return user_has_any_role(user, 'COMMANDER', 'EOC')


def can_manage_users(user) -> bool:
    return user_has_any_role(user, 'ADMIN')


def can_view_analytics(user) -> bool:
    return user_has_any_role(user, 'ADMIN', 'COORDINATOR', 'COMMANDER', 'EOC')


def can_manage_facilities(user) -> bool:
    """Adding facilities to the directory is an EOC operational action.
    Admin is intentionally excluded -- same boundary as can_issue_alert()."""
    return user_has_any_role(user, 'EOC')


def can_manage_evacuation_centers(user) -> bool:
    return user_has_any_role(user, 'EOC', 'COORDINATOR')


def can_request_resources(user) -> bool:
    """Submitting an agency resource request is a coordinator action -- it's
    asking EOC/Commander for something this coordinator's own agency
    doesn't have on hand. Admin has no agency of its own to request on
    behalf of, so it's intentionally excluded (see is_agency_coordinator()
    in blueprints/common.py for the same boundary on the rest of the
    coordinator dashboard)."""
    return user_has_any_role(user, 'COORDINATOR')


def can_decide_resource_request(user) -> bool:
    return user_has_any_role(user, 'ADMIN', 'EOC', 'COMMANDER')


def can_log_incident_report(user) -> bool:
    return user_has_any_role(user, 'ADMIN', 'EOC')
