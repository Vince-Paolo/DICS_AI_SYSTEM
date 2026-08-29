from werkzeug.security import check_password_hash, generate_password_hash


def verify_and_migrate(user, password, commit, rollback, log):
    """Verify a password and transparently upgrade legacy plaintext values."""
    if user is None or not password:
        return False

    stored = user.password
    if not stored:
        return False

    try:
        if check_password_hash(stored, password):
            return True
    except (ValueError, TypeError):
        pass
    except Exception:
        log.exception('Unexpected error while checking password for user %s', user.username)

    if stored != password:
        return False

    user.password = generate_password_hash(password)
    try:
        commit()
    except Exception:
        rollback()
        log.exception('Failed to migrate legacy password for user %s', user.username)
        return False
    return True
