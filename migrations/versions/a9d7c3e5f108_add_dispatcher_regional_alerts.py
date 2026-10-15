"""add dispatcher-only regional event alerts

Revision ID: a9d7c3e5f108
Revises: f4c1a9e2b730
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9d7c3e5f108'
down_revision = 'f4c1a9e2b730'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('alert') as batch:
        batch.add_column(sa.Column(
            'audience',
            sa.String(length=20),
            nullable=False,
            server_default='PUBLIC',
        ))
        batch.add_column(sa.Column(
            'external_event_id',
            sa.String(length=255),
            nullable=True,
        ))
        batch.create_unique_constraint(
            'uq_alert_external_event_id',
            ['external_event_id'],
        )


def downgrade():
    connection = op.get_bind()
    has_dispatcher_alerts = connection.execute(sa.text(
        "SELECT COUNT(*) FROM alert WHERE audience = 'DISPATCHER'"
    )).scalar_one()
    if has_dispatcher_alerts:
        raise RuntimeError(
            'Cannot remove dispatcher alert audiences while dispatcher-only alerts exist; '
            'export or remove those alerts before downgrading to avoid exposing them as public.'
        )

    with op.batch_alter_table('alert') as batch:
        batch.drop_constraint('uq_alert_external_event_id', type_='unique')
        batch.drop_column('external_event_id')
        batch.drop_column('audience')
