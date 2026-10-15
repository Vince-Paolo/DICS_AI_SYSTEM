"""add task remarks and migrate failed tasks

Revision ID: c4a72f8d1e63
Revises: 9a1b2c3d4e5f
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4a72f8d1e63'
down_revision = '9a1b2c3d4e5f'
branch_labels = None
depends_on = None

MIGRATED_FAILED_REMARK = 'Previously marked FAILED; migrated to COMPLETED during Phase 1.'


def _task_columns():
    inspector = sa.inspect(op.get_bind())
    if 'task' not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns('task')}


def upgrade():
    columns = _task_columns()
    if not columns:
        return

    if 'remarks' not in columns:
        op.add_column('task', sa.Column('remarks', sa.Text(), nullable=True))
        columns.add('remarks')

    if 'status' not in columns:
        return

    version_update = ', version_id = version_id + 1' if 'version_id' in columns else ''
    op.get_bind().execute(sa.text(
        "UPDATE task "
        "SET status = 'COMPLETED', "
        "remarks = CASE "
        "WHEN remarks IS NULL OR TRIM(remarks) = '' THEN :remark "
        "ELSE remarks END"
        f"{version_update} "
        "WHERE UPPER(TRIM(status)) = 'FAILED'"
    ), {'remark': MIGRATED_FAILED_REMARK})


def downgrade():
    columns = _task_columns()
    if 'remarks' not in columns:
        return

    if 'status' in columns:
        version_update = ', version_id = version_id + 1' if 'version_id' in columns else ''
        op.get_bind().execute(sa.text(
            "UPDATE task "
            "SET status = 'FAILED'"
            f"{version_update} "
            "WHERE status = 'COMPLETED' AND remarks = :remark"
        ), {'remark': MIGRATED_FAILED_REMARK})

    with op.batch_alter_table('task') as batch_op:
        batch_op.drop_column('remarks')
