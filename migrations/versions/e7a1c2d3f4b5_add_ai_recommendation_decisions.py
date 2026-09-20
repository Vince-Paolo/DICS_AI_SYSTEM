"""add commander decisions to ai recommendations

Revision ID: e7a1c2d3f4b5
Revises: d3f7b1a9c4e2
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


revision = 'e7a1c2d3f4b5'
down_revision = 'd3f7b1a9c4e2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_recommendation', sa.Column('decision', sa.String(length=20), nullable=True))
    op.add_column('ai_recommendation', sa.Column('decision_reason', sa.Text(), nullable=True))
    op.add_column('ai_recommendation', sa.Column('decided_by_id', sa.Integer(), nullable=True))
    op.add_column('ai_recommendation', sa.Column('decided_at', sa.DateTime(), nullable=True))
    op.create_foreign_key(
        'fk_ai_recommendation_decided_by_id_user',
        'ai_recommendation',
        'user',
        ['decided_by_id'],
        ['id'],
    )


def downgrade():
    op.drop_constraint('fk_ai_recommendation_decided_by_id_user', 'ai_recommendation', type_='foreignkey')
    op.drop_column('ai_recommendation', 'decided_at')
    op.drop_column('ai_recommendation', 'decided_by_id')
    op.drop_column('ai_recommendation', 'decision_reason')
    op.drop_column('ai_recommendation', 'decision')
