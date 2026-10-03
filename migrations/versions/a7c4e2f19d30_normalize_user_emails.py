"""normalize user emails and enforce case-insensitive uniqueness

Revision ID: a7c4e2f19d30
Revises: f2b6d8a1c3e5
Create Date: 2026-09-30

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c4e2f19d30'
down_revision = 'f2b6d8a1c3e5'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicate = connection.execute(sa.text(
        'SELECT lower(trim(email)) FROM "user" '
        'GROUP BY lower(trim(email)) HAVING COUNT(*) > 1 LIMIT 1'
    )).first()
    if duplicate:
        raise RuntimeError(
            'Cannot enforce case-insensitive user email uniqueness: existing duplicate emails '
            'differ only by case or whitespace. Resolve those accounts before upgrading.'
        )

    connection.execute(sa.text('UPDATE "user" SET email = lower(trim(email))'))
    op.create_index(
        'uq_user_email_lower',
        'user',
        [sa.text('lower(email)')],
        unique=True,
    )


def downgrade():
    op.drop_index('uq_user_email_lower', table_name='user')