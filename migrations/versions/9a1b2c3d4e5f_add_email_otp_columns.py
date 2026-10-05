"""add email OTP verification columns to user

Revision ID: 9a1b2c3d4e5f
Revises: d8f2b7c1e609
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = '9a1b2c3d4e5f'
down_revision = 'd8f2b7c1e609'
branch_labels = None
depends_on = None


def _user_columns():
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user')}


def upgrade():
    cols = _user_columns()
    with op.batch_alter_table('user') as batch_op:
        if 'verification_token' not in cols:
            batch_op.add_column(sa.Column('verification_token', sa.String(length=500), nullable=True))
        if 'verification_expires_at' not in cols:
            batch_op.add_column(sa.Column('verification_expires_at', sa.DateTime(), nullable=True))
        if 'verification_attempts' not in cols:
            batch_op.add_column(sa.Column('verification_attempts', sa.Integer(), nullable=True, server_default='0'))
        if 'verification_sent_at' not in cols:
            batch_op.add_column(sa.Column('verification_sent_at', sa.DateTime(), nullable=True))


def downgrade():
    cols = _user_columns()
    with op.batch_alter_table('user') as batch_op:
        for name in ('verification_sent_at', 'verification_attempts', 'verification_expires_at'):
            if name in cols:
                batch_op.drop_column(name)
