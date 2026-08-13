"""Create study_generation_runs orchestration table

Revision ID: 20260326_0004
Revises: 20260325_0003
Create Date: 2026-03-26

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "20260326_0004"
down_revision: Union[str, Sequence[str], None] = "20260325_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "study_generation_runs" in inspector.get_table_names():
        return

    op.create_table(
        "study_generation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("upstream_job_id", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("preview_url", sa.String(length=500), nullable=True),
        sa.Column("share_url", sa.String(length=500), nullable=True),
        sa.Column("study_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column(
            "snapshot_json",
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
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_study_generation_runs_chat_id",
        "study_generation_runs",
        ["chat_id"],
    )
    op.create_index(
        "ix_study_generation_runs_chat_id_created_at",
        "study_generation_runs",
        ["chat_id", "created_at"],
    )
    op.create_index(
        "ix_study_generation_runs_study_id",
        "study_generation_runs",
        ["study_id"],
    )
    op.create_index(
        "ix_study_generation_runs_upstream_job_id",
        "study_generation_runs",
        ["upstream_job_id"],
    )
    op.create_index(
        "ix_study_generation_runs_project_id",
        "study_generation_runs",
        ["project_id"],
    )
    op.create_index(
        "ix_study_generation_runs_user_id",
        "study_generation_runs",
        ["user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "study_generation_runs" not in inspector.get_table_names():
        return
    op.drop_index("ix_study_generation_runs_user_id", table_name="study_generation_runs")
    op.drop_index("ix_study_generation_runs_project_id", table_name="study_generation_runs")
    op.drop_index("ix_study_generation_runs_upstream_job_id", table_name="study_generation_runs")
    op.drop_index("ix_study_generation_runs_study_id", table_name="study_generation_runs")
    op.drop_index(
        "ix_study_generation_runs_chat_id_created_at",
        table_name="study_generation_runs",
    )
    op.drop_index("ix_study_generation_runs_chat_id", table_name="study_generation_runs")
    op.drop_table("study_generation_runs")
