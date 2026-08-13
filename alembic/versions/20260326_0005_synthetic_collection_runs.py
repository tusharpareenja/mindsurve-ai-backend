"""Create synthetic_collection_runs orchestration table

Revision ID: 20260326_0005
Revises: 20260326_0004
Create Date: 2026-03-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "20260326_0005"
down_revision: Union[str, Sequence[str], None] = "20260326_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "synthetic_collection_runs" in inspector.get_table_names():
        return

    op.create_table(
        "synthetic_collection_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("upstream_job_id", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="ai"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("respondents_requested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("respondents_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "stats_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_synthetic_collection_runs_chat_id",
        "synthetic_collection_runs",
        ["chat_id"],
    )
    op.create_index(
        "ix_synthetic_collection_runs_chat_id_created_at",
        "synthetic_collection_runs",
        ["chat_id", "created_at"],
    )
    op.create_index(
        "ix_synthetic_collection_runs_study_id",
        "synthetic_collection_runs",
        ["study_id"],
    )
    op.create_index(
        "ix_synthetic_collection_runs_upstream_job_id",
        "synthetic_collection_runs",
        ["upstream_job_id"],
    )
    op.create_index(
        "ix_synthetic_collection_runs_project_id",
        "synthetic_collection_runs",
        ["project_id"],
    )
    op.create_index(
        "ix_synthetic_collection_runs_user_id",
        "synthetic_collection_runs",
        ["user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "synthetic_collection_runs" not in inspector.get_table_names():
        return
    op.drop_index("ix_synthetic_collection_runs_user_id", table_name="synthetic_collection_runs")
    op.drop_index("ix_synthetic_collection_runs_project_id", table_name="synthetic_collection_runs")
    op.drop_index(
        "ix_synthetic_collection_runs_upstream_job_id",
        table_name="synthetic_collection_runs",
    )
    op.drop_index("ix_synthetic_collection_runs_study_id", table_name="synthetic_collection_runs")
    op.drop_index(
        "ix_synthetic_collection_runs_chat_id_created_at",
        table_name="synthetic_collection_runs",
    )
    op.drop_index("ix_synthetic_collection_runs_chat_id", table_name="synthetic_collection_runs")
    op.drop_table("synthetic_collection_runs")
