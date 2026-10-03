"""add optimistic locking versions

Revision ID: 5b08953aa7d2
Revises: 46f03dfa7e95
"""
from alembic import op
import sqlalchemy as sa


revision = '5b08953aa7d2'
down_revision = '46f03dfa7e95'
branch_labels = None
depends_on = None


def upgrade():
    for table_name in ('task', 'resource', 'evacuation_center'):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column('version_id', sa.Integer(), nullable=False, server_default='1')
            )


def downgrade():
    for table_name in ('evacuation_center', 'resource', 'task'):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column('version_id')
