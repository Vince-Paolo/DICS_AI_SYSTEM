"""hash legacy plaintext user passwords

Revision ID: b3c8d4e2f190
Revises: a7c4e2f19d30
Create Date: 2026-09-30

"""
from alembic import op
import sqlalchemy as sa
from werkzeug.security import generate_password_hash


revision = 'b3c8d4e2f190'
down_revision = 'a7c4e2f19d30'
branch_labels = None
depends_on = None


def _is_werkzeug_hash(value):
    if not isinstance(value, str):
        return False
    parts = value.split('$')
    return len(parts) == 3 and parts[0].startswith(('scrypt:', 'pbkdf2:'))


def upgrade():
    connection = op.get_bind()
    users = connection.execute(sa.text('SELECT id, password FROM "user"')).all()
    updates = [
        {'user_id': user_id, 'password_hash': generate_password_hash(password)}
        for user_id, password in users
        if password and not _is_werkzeug_hash(password)
    ]
    if updates:
        connection.execute(sa.text(
            'UPDATE "user" SET password = :password_hash WHERE id = :user_id'
        ), updates)


def downgrade():
    pass