"""add gps_accuracy to citizen_report

Revision ID: d3f7b1a9c4e2
Revises: c1d4e6f8a2b0
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa


revision = 'd3f7b1a9c4e2'
down_revision = 'c1d4e6f8a2b0'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('citizen_report', sa.Column('gps_accuracy', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('citizen_report', 'gps_accuracy')
