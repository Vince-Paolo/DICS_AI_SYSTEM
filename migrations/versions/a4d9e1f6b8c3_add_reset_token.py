"""add password reset token to user

Revision ID: a4d9e1f6b8c3
Revises: f2b6d8a1c3e5
Create Date: 2026-09-22

"""
from alembic import op
import sqlalchemy as sa


revision = 'a4d9e1f6b8c3'
down_revision = 'f2b6d8a1c3e5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('reset_token', sa.String(length=500), nullable=True))
    op.add_column('user', sa.Column('reset_token_expires_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('user', 'reset_token_expires_at')
    op.drop_column('user', 'reset_token')
