"""add dispatcher teams and barangay contacts

Revision ID: e3b2d7a4c891
Revises: d1e6a94b7c32
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = 'e3b2d7a4c891'
down_revision = 'd1e6a94b7c32'
branch_labels = None
depends_on = None


def _tables(connection):
    return set(sa.inspect(connection).get_table_names())


def upgrade():
    connection = op.get_bind()
    tables = _tables(connection)

    if 'incident_response' in tables:
        response_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('incident_response')
        }
        with op.batch_alter_table('incident_response') as batch:
            if 'dispatched_by_id' not in response_columns:
                batch.add_column(sa.Column('dispatched_by_id', sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    'fk_incident_response_dispatched_by_user',
                    'user',
                    ['dispatched_by_id'],
                    ['id'],
                )
            commander_column = response_columns.get('commander_id')
            if commander_column and not commander_column.get('nullable', True):
                batch.alter_column(
                    'commander_id',
                    existing_type=commander_column['type'],
                    nullable=True,
                )
        indexes = {index['name'] for index in sa.inspect(connection).get_indexes('incident_response')}
        if 'ix_incident_response_dispatched_by_id' not in indexes:
            op.create_index(
                'ix_incident_response_dispatched_by_id',
                'incident_response',
                ['dispatched_by_id'],
            )

    if 'response_team' not in _tables(connection):
        op.create_table(
            'response_team',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(length=120), nullable=False, unique=True),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if 'task' in tables:
        task_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('task')
        }
        with op.batch_alter_table('task') as batch:
            if 'assigned_to_user_id' not in task_columns:
                batch.add_column(sa.Column('assigned_to_user_id', sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    'fk_task_assigned_to_user',
                    'user',
                    ['assigned_to_user_id'],
                    ['id'],
                )
            if 'assigned_to_team_id' not in task_columns:
                batch.add_column(sa.Column('assigned_to_team_id', sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    'fk_task_assigned_to_team',
                    'response_team',
                    ['assigned_to_team_id'],
                    ['id'],
                )
            agency_column = task_columns.get('assigned_to_agency')
            if agency_column and not agency_column.get('nullable', True):
                batch.alter_column(
                    'assigned_to_agency',
                    existing_type=agency_column['type'],
                    nullable=True,
                )

    if 'response_team_members' not in _tables(connection):
        op.create_table(
            'response_team_members',
            sa.Column('team_id', sa.Integer(), sa.ForeignKey('response_team.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        )

    if 'barangay_response_unit' not in _tables(connection):
        op.create_table(
            'barangay_response_unit',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('barangay_id', sa.Integer(), sa.ForeignKey('barangay.id', ondelete='CASCADE'), nullable=False, unique=True),
            sa.Column('contact_person', sa.String(length=150), nullable=True),
            sa.Column('contact_number', sa.String(length=30), nullable=True),
            sa.Column('team_size', sa.Integer(), nullable=True),
            sa.Column('equipment', sa.Text(), nullable=True),
            sa.Column('location', sa.String(length=255), nullable=True),
            sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if 'barangay_contact_log' not in _tables(connection):
        op.create_table(
            'barangay_contact_log',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incident.id', ondelete='CASCADE'), nullable=False),
            sa.Column('response_unit_id', sa.Integer(), sa.ForeignKey('barangay_response_unit.id'), nullable=False),
            sa.Column('contacted_person', sa.String(length=150), nullable=True),
            sa.Column('contact_method', sa.String(length=20), nullable=False, server_default='PHONE'),
            sa.Column('outcome', sa.String(length=40), nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('contacted_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('logged_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        )

    for table_name, column_name, index_name in (
        ('task', 'assigned_to_user_id', 'ix_task_assigned_to_user_id'),
        ('task', 'assigned_to_team_id', 'ix_task_assigned_to_team_id'),
        ('barangay_contact_log', 'incident_id', 'ix_barangay_contact_log_incident_id'),
        ('barangay_contact_log', 'response_unit_id', 'ix_barangay_contact_log_response_unit_id'),
        ('barangay_contact_log', 'contacted_at', 'ix_barangay_contact_log_contacted_at'),
    ):
        if table_name not in _tables(connection):
            continue
        indexes = {index['name'] for index in sa.inspect(connection).get_indexes(table_name)}
        if index_name not in indexes:
            op.create_index(index_name, table_name, [column_name])


def downgrade():
    connection = op.get_bind()
    tables = _tables(connection)
    task_columns = {}
    if 'task' in tables:
        task_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('task')
        }
        assignment_columns = [
            column for column in ('assigned_to_user_id', 'assigned_to_team_id')
            if column in task_columns
        ]
        if assignment_columns:
            assigned_condition = ' OR '.join(
                f'"{column}" IS NOT NULL' for column in assignment_columns
            )
            assigned_tasks = connection.execute(sa.text(
                f'SELECT COUNT(*) FROM task WHERE {assigned_condition}'
            )).scalar_one()
            if assigned_tasks:
                raise RuntimeError(
                    'Cannot downgrade while tasks are assigned to Dispatchers, responders, or teams.'
                )
        agency_column = task_columns.get('assigned_to_agency')
        if agency_column and agency_column.get('nullable', True):
            null_agency_tasks = connection.execute(sa.text(
                'SELECT COUNT(*) FROM task WHERE assigned_to_agency IS NULL'
            )).scalar_one()
            if null_agency_tasks:
                raise RuntimeError(
                    'Cannot downgrade while tasks have no legacy agency assignment.'
                )

    if 'incident_response' in tables:
        response_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('incident_response')
        }
        if 'commander_id' in response_columns:
            dispatcher_owned = connection.execute(sa.text(
                'SELECT COUNT(*) FROM incident_response WHERE commander_id IS NULL'
            )).scalar_one()
            if dispatcher_owned:
                raise RuntimeError(
                    'Cannot downgrade while dispatcher-owned incident responses exist.'
                )

    for table_name, index_name in (
        ('barangay_contact_log', 'ix_barangay_contact_log_incident_id'),
        ('barangay_contact_log', 'ix_barangay_contact_log_response_unit_id'),
        ('barangay_contact_log', 'ix_barangay_contact_log_contacted_at'),
        ('task', 'ix_task_assigned_to_user_id'),
        ('task', 'ix_task_assigned_to_team_id'),
        ('incident_response', 'ix_incident_response_dispatched_by_id'),
    ):
        if table_name in tables:
            indexes = {index['name'] for index in sa.inspect(connection).get_indexes(table_name)}
            if index_name in indexes:
                op.drop_index(index_name, table_name=table_name)

    if 'barangay_contact_log' in tables:
        op.drop_table('barangay_contact_log')
    if 'barangay_response_unit' in tables:
        op.drop_table('barangay_response_unit')
    if 'response_team_members' in tables:
        op.drop_table('response_team_members')

    if 'task' in tables:
        task_columns = {column['name'] for column in sa.inspect(connection).get_columns('task')}
        with op.batch_alter_table('task') as batch:
            if 'assigned_to_user_id' in task_columns:
                batch.drop_column('assigned_to_user_id')
            if 'assigned_to_team_id' in task_columns:
                batch.drop_column('assigned_to_team_id')

    if 'response_team' in tables:
        op.drop_table('response_team')

    if 'incident_response' in tables:
        response_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('incident_response')
        }
        with op.batch_alter_table('incident_response') as batch:
            if 'dispatched_by_id' in response_columns:
                batch.drop_column('dispatched_by_id')
            commander_column = response_columns.get('commander_id')
            if commander_column and commander_column.get('nullable', True):
                batch.alter_column(
                    'commander_id',
                    existing_type=commander_column['type'],
                    nullable=False,
                )

    if 'task' in tables:
        task_columns = {
            column['name']: column for column in sa.inspect(connection).get_columns('task')
        }
        agency_column = task_columns.get('assigned_to_agency')
        if agency_column and agency_column.get('nullable', True):
            with op.batch_alter_table('task') as batch:
                batch.alter_column(
                    'assigned_to_agency',
                    existing_type=agency_column['type'],
                    nullable=False,
                )
