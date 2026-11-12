"""add bl_number to containers

Revision ID: c4d1a7e92b35
Revises: 54fd2ada8ece
Create Date: 2026-11-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d1a7e92b35'
down_revision: Union[str, Sequence[str], None] = '54fd2ada8ece'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('containers', sa.Column('bl_number', sa.String(), nullable=True, comment='Номер коносамента'))
    op.create_index(op.f('ix_containers_bl_number'), 'containers', ['bl_number'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_containers_bl_number'), table_name='containers')
    op.drop_column('containers', 'bl_number')
