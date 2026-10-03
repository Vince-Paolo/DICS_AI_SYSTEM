"""add Filipino translations for incident messages

Revision ID: d8f2b7c1e609
Revises: afe5c72536f6
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa


revision = 'd8f2b7c1e609'
down_revision = 'afe5c72536f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('incident', sa.Column('message_fil', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('incident', 'message_fil')
