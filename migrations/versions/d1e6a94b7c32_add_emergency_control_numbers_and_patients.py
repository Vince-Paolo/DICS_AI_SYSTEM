"""add emergency control numbers and patient records

Revision ID: d1e6a94b7c32
Revises: c4a72f8d1e63
Create Date: 2026-10-11
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision = 'd1e6a94b7c32'
down_revision = 'c4a72f8d1e63'
branch_labels = None
depends_on = None
MANILA_TIMEZONE = ZoneInfo('Asia/Manila')


def _table_names(connection):
    return set(sa.inspect(connection).get_table_names())


def _next_backfill_number(connection, sequence_table, year):
    dialect = connection.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Control number backfill is not supported for {dialect}.')

    statement = insert(sequence_table).values(year=year, last_number=1).on_conflict_do_update(
        index_elements=[sequence_table.c.year],
        set_={'last_number': sequence_table.c.last_number + 1},
    ).returning(sequence_table.c.last_number)
    return connection.execute(statement).scalar_one()


def _incident_year(row):
    created_at = row['created_at'] or row['event_time']
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(MANILA_TIMEZONE).year


def upgrade():
    connection = op.get_bind()
    tables = _table_names(connection)
    if 'incident' not in tables:
        return

    if 'control_number_sequence' not in tables:
        op.create_table(
            'control_number_sequence',
            sa.Column('year', sa.Integer(), primary_key=True),
            sa.Column('last_number', sa.Integer(), nullable=False),
        )

    incident_columns = {
        column['name'] for column in sa.inspect(connection).get_columns('incident')
    }
    with op.batch_alter_table('incident') as batch_op:
        if 'control_number' not in incident_columns:
            batch_op.add_column(sa.Column('control_number', sa.String(length=20), nullable=True))
        if 'patient_count' not in incident_columns:
            batch_op.add_column(
                sa.Column('patient_count', sa.Integer(), nullable=False, server_default='0')
            )

    if 'patient_record' not in _table_names(connection):
        op.create_table(
            'patient_record',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('incident_id', sa.Integer(), sa.ForeignKey('incident.id', ondelete='CASCADE'), nullable=False),
            sa.Column('condition', sa.String(length=255), nullable=False),
            sa.Column('destination', sa.String(length=255), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=False, server_default='RECEIVING_CARE'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_patient_record_incident_id', 'patient_record', ['incident_id'])

    sequence_table = sa.Table(
        'control_number_sequence',
        sa.MetaData(),
        autoload_with=connection,
    )
    incident_table = sa.Table('incident', sa.MetaData(), autoload_with=connection)
    rows = connection.execute(sa.select(
        incident_table.c.id,
        incident_table.c.control_number,
        incident_table.c.created_at,
        incident_table.c.event_time,
    ).order_by(
        incident_table.c.created_at,
        incident_table.c.id,
    )).mappings()

    for row in rows:
        control_number = row['control_number']
        if not control_number:
            year = _incident_year(row)
            sequence = _next_backfill_number(connection, sequence_table, year)
            control_number = f'{year}-{sequence:05d}'
            connection.execute(
                incident_table.update()
                .where(incident_table.c.id == row['id'])
                .values(control_number=control_number)
            )

        try:
            year_text, sequence_text = control_number.split('-', 1)
            year, sequence = int(year_text), int(sequence_text)
        except (AttributeError, ValueError):
            continue

        current = connection.execute(
            sa.select(sequence_table.c.last_number)
            .where(sequence_table.c.year == year)
        ).scalar_one_or_none()
        if current is None:
            connection.execute(sequence_table.insert().values(year=year, last_number=sequence))
        elif sequence > current:
            connection.execute(
                sequence_table.update()
                .where(sequence_table.c.year == year)
                .values(last_number=sequence)
            )

    indexes = {index['name'] for index in sa.inspect(connection).get_indexes('incident')}
    if 'uq_incident_control_number' not in indexes:
        op.create_index('uq_incident_control_number', 'incident', ['control_number'], unique=True)
    if connection.dialect.name == 'postgresql':
        op.alter_column(
            'incident',
            'control_number',
            existing_type=sa.String(length=20),
            nullable=False,
        )


def downgrade():
    connection = op.get_bind()
    tables = _table_names(connection)
    if 'incident' not in tables:
        return

    if 'patient_record' in tables:
        op.drop_index('ix_patient_record_incident_id', table_name='patient_record')
        op.drop_table('patient_record')

    indexes = {index['name'] for index in sa.inspect(connection).get_indexes('incident')}
    if 'uq_incident_control_number' in indexes:
        op.drop_index('uq_incident_control_number', table_name='incident')

    columns = {column['name'] for column in sa.inspect(connection).get_columns('incident')}
    with op.batch_alter_table('incident') as batch_op:
        if 'patient_count' in columns:
            batch_op.drop_column('patient_count')
        if 'control_number' in columns:
            batch_op.drop_column('control_number')

    if 'control_number_sequence' in tables:
        op.drop_table('control_number_sequence')
