"""scope operational roles and resources to San Pablo CDRRMO

Revision ID: f4c1a9e2b730
Revises: e3b2d7a4c891
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4c1a9e2b730'
down_revision = 'e3b2d7a4c891'
branch_labels = None
depends_on = None


def _tables(connection):
    return set(sa.inspect(connection).get_table_names())


def _columns(connection, table_name):
    return {
        column['name']: column
        for column in sa.inspect(connection).get_columns(table_name)
    }


def _drop_columns(table_name, column_names):
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    columns_to_drop = set(column_names)
    with op.batch_alter_table(table_name) as batch:
        for index in indexes:
            indexed_columns = set(index.get('column_names') or ())
            if index.get('name') and indexed_columns & columns_to_drop:
                batch.drop_index(index['name'])
        for name in column_names:
            batch.drop_column(name)


def upgrade():
    connection = op.get_bind()
    tables = _tables(connection)

    if 'user' in tables:
        user_columns = _columns(connection, 'user')
        if {'role', 'is_disabled'}.issubset(user_columns):
            connection.execute(sa.text(
                'UPDATE "user" SET role = \'retired\', is_disabled = TRUE '
                "WHERE lower(role) IN ('agency_coordinator', 'coordinator')"
            ))
            connection.execute(sa.text(
                'UPDATE "user" SET role = \'imt_staff\' '
                "WHERE lower(role) IN ('incident_commander', 'commander')"
            ))
        if 'agency' in user_columns:
            _drop_columns('user', ('agency',))

    if 'task' in tables and 'assigned_to_agency' in _columns(connection, 'task'):
        legacy_assignments = connection.execute(sa.text(
            'SELECT id, remarks, assigned_to_agency FROM "task" '
            "WHERE assigned_to_agency IS NOT NULL AND assigned_to_agency <> ''"
        )).mappings().all()
        for task in legacy_assignments:
            historical_note = f"Historical agency assignment: {task['assigned_to_agency']}"
            remarks = task['remarks']
            updated_remarks = f'{remarks}\n{historical_note}' if remarks else historical_note
            connection.execute(sa.text(
                'UPDATE "task" SET remarks = :remarks WHERE id = :task_id'
            ), {'remarks': updated_remarks, 'task_id': task['id']})
        _drop_columns('task', ('assigned_to_agency',))

    if 'resource' in tables:
        resource_columns = _columns(connection, 'resource')
        removable = [
            name for name in ('resource_request_id', 'agency')
            if name in resource_columns
        ]
        if removable:
            _drop_columns('resource', removable)

    if 'resource_request' in tables:
        op.drop_table('resource_request')

    if 'ai_recommendation' in tables and 'recommended_agencies' in _columns(connection, 'ai_recommendation'):
        _drop_columns('ai_recommendation', ('recommended_agencies',))

    if 'agency' in _tables(connection):
        op.drop_table('agency')


def downgrade():
    raise RuntimeError(
        'Phase 4 removes historical resource requests and retires coordinator accounts; '
        'restore a database backup to reverse this migration.'
    )
