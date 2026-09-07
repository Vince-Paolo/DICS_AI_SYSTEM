"""link resource requests to resources and add evacuation records

Revision ID: f2b6d8a1c3e5
Revises: e7a1c2d3f4b5
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b6d8a1c3e5'
down_revision = 'e7a1c2d3f4b5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('resource', schema=None) as batch_op:
        batch_op.add_column(sa.Column('resource_request_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_resource_resource_request_id', ['resource_request_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_resource_resource_request_id',
            'resource_request',
            ['resource_request_id'],
            ['id'],
            ondelete='SET NULL',
        )

    op.create_table(
        'evacuation_record',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('incident_response_id', sa.Integer(), nullable=False),
        sa.Column('evacuation_center_id', sa.Integer(), nullable=False),
        sa.Column('people_count', sa.Integer(), nullable=False),
        sa.Column('recorded_by_id', sa.Integer(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['incident_response_id'], ['incident_response.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['evacuation_center_id'], ['evacuation_center.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recorded_by_id'], ['user.id']),
        sa.CheckConstraint('people_count > 0', name='ck_evacuation_record_people_positive'),
    )
    op.create_index('ix_evacuation_record_incident_response_id', 'evacuation_record', ['incident_response_id'])
    op.create_index('ix_evacuation_record_evacuation_center_id', 'evacuation_record', ['evacuation_center_id'])
    op.create_index('ix_evacuation_record_recorded_at', 'evacuation_record', ['recorded_at'])


def downgrade():
    op.drop_index('ix_evacuation_record_recorded_at', table_name='evacuation_record')
    op.drop_index('ix_evacuation_record_evacuation_center_id', table_name='evacuation_record')
    op.drop_index('ix_evacuation_record_incident_response_id', table_name='evacuation_record')
    op.drop_table('evacuation_record')

    with op.batch_alter_table('resource', schema=None) as batch_op:
        batch_op.drop_constraint('fk_resource_resource_request_id', type_='foreignkey')
        batch_op.drop_index('ix_resource_resource_request_id')
        batch_op.drop_column('resource_request_id')
