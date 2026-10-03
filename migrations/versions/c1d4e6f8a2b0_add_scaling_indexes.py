"""add indexes for operational list queries

Revision ID: c1d4e6f8a2b0
Revises: 5b08953aa7d2, 8c7a4f2d1e90
Create Date: 2026-08-27

"""
from alembic import op


revision = 'c1d4e6f8a2b0'
down_revision = ('5b08953aa7d2', '8c7a4f2d1e90')
branch_labels = None
depends_on = None


INDEXES = (
    ('ix_task_assigned_to_agency', 'task', ['assigned_to_agency']),
    ('ix_task_created_at', 'task', ['created_at']),
    ('ix_incident_response_status', 'incident_response', ['status']),
    ('ix_incident_response_started_at', 'incident_response', ['started_at']),
    ('ix_incident_message_incident_response_id', 'incident_message', ['incident_response_id']),
    ('ix_incident_message_created_at', 'incident_message', ['created_at']),
    ('ix_resource_agency', 'resource', ['agency']),
    ('ix_resource_allocated_at', 'resource', ['allocated_at']),
    ('ix_resource_request_agency', 'resource_request', ['agency']),
    ('ix_resource_request_created_at', 'resource_request', ['created_at']),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
