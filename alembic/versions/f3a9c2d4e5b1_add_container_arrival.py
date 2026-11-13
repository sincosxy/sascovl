"""add container arrival at terminal

Revision ID: f3a9c2d4e5b1
Revises: e7b2f4c81d06
Create Date: 2026-11-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c2d4e5b1'
down_revision: Union[str, Sequence[str], None] = 'e7b2f4c81d06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('containers', sa.Column('arrived_at', sa.Date(), nullable=True, comment='Дата поступления на терминал'))
    op.add_column('containers', sa.Column('arrival_confirmed_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_containers_arrival_confirmed_by_id_users', 'containers', 'users',
        ['arrival_confirmed_by_id'], ['id'],
    )
    # Контейнеры, которые уже стоят в рейсах, считаем принятыми — иначе они «сломаются»
    # (окажутся в рейсе без поступления). Дату берём сегодняшнюю: точной у нас нет.
    op.execute("UPDATE containers SET arrived_at = CURRENT_DATE WHERE voyage_id IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_containers_arrival_confirmed_by_id_users', 'containers', type_='foreignkey')
    op.drop_column('containers', 'arrival_confirmed_by_id')
    op.drop_column('containers', 'arrived_at')
