"""Create study_brief_versions draft history table

Revision ID: 20260814_0006
Revises: 20260326_0005
Create Date: 2026-08-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "20260814_0006"
down_revision: Union[str, Sequence[str], None] = "20260326_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "study_brief_versions" in inspector.get_table_names():
        return

    op.create_table(
        "study_brief_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="ai"),
        sa.Column(
            "brief_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "changed_fields",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "version", name="uq_study_brief_versions_chat_version"),
    )
    op.create_index(
        "ix_study_brief_versions_chat_id",
        "study_brief_versions",
        ["chat_id"],
    )
    op.create_index(
        "ix_study_brief_versions_chat_id_version",
        "study_brief_versions",
        ["chat_id", "version"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "study_brief_versions" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_study_brief_versions_chat_id_version",
        table_name="study_brief_versions",
    )
    op.drop_index("ix_study_brief_versions_chat_id", table_name="study_brief_versions")
    op.drop_table("study_brief_versions")
