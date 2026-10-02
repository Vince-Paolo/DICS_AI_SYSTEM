from werkzeug.security import check_password_hash


def verify_and_migrate(user, password, commit, rollback, log):
    """Verify only Werkzeug password hashes; plaintext values are never valid."""
    if user is None or not password:
        return False

    stored = user.password
    if not stored:
        return False

    try:
        return check_password_hash(stored, password)
    except (ValueError, TypeError):
        return False
    except Exception:
        log.exception('Unexpected error while checking password for user %s', user.username)
        return False
