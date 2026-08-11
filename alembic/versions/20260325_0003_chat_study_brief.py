"""Add chats.study_brief JSON column

Revision ID: 20260325_0003
Revises: 20260324_0002
Create Date: 2026-03-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "20260325_0003"
down_revision: Union[str, Sequence[str], None] = "20260324_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "chats" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "study_brief" in cols:
        return
    op.add_column(
        "chats",
        sa.Column(
            "study_brief",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "chats" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("chats")}
    if "study_brief" in cols:
        op.drop_column("chats", "study_brief")
