import services.permissions as permission_service


def current_user():
    return permission_service.current_user()


def is_admin():
    """Backward-compatible helper delegating to the shared permission module."""
    return permission_service.is_admin()


def is_field_responder():
    return permission_service.is_responder()


def is_eoc_staff():
    return permission_service.is_eoc()


def is_admin_or_eoc():
    """Check if user is admin or EOC staff (for dispatch-style operations:
    verifying incidents, assigning/transferring commanders, toggling alerts)."""
    return is_admin() or is_eoc_staff()


def can_manage_response(response):
    if not response:
        return False
    if permission_service.is_dispatcher():
        return True
    if is_admin():
        return True
    return permission_service.is_imt()


def can_view_response(response):
    if not response:
        return False
    if is_admin() or is_eoc_staff() or permission_service.is_dispatcher():
        return True
    return can_manage_response(response)
