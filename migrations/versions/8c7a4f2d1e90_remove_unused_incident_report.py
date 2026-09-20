"""remove unused incident report model

Revision ID: 8c7a4f2d1e90
Revises: 46f03dfa7e95
Create Date: 2026-08-27

"""
from alembic import op
import sqlalchemy as sa


revision = '8c7a4f2d1e90'
down_revision = '46f03dfa7e95'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('incident_report')


def downgrade():
    op.create_table(
        'incident_report',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incident_id', sa.Integer(), nullable=False),
        sa.Column('reporter_id', sa.Integer(), nullable=True),
        sa.Column('report_type', sa.String(length=50), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incident.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reporter_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )