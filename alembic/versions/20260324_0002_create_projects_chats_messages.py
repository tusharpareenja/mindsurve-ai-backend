"""Shared projects extensions + chats / chat_messages

Revision ID: 20260324_0002
Revises: 20260324_0001
Create Date: 2026-03-24

- Reuses Unilever `projects` when present; adds MindSurve columns only.
- Creates `chats` + `chat_messages` (MindSurve-owned).

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "20260324_0002"
down_revision: Union[str, Sequence[str], None] = "20260324_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(inspector: object, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}  # type: ignore[attr-defined]


def _index_names(inspector: object, table: str) -> set[str]:
    return {
        i["name"]
        for i in inspector.get_indexes(table)  # type: ignore[attr-defined]
        if i.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "projects" not in tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("creator_id", sa.Uuid(), nullable=False),
            sa.Column("idea", sa.Text(), nullable=True),
            sa.Column(
                "workflow_type",
                sa.String(length=32),
                nullable=False,
                server_default="beginner",
            ),
            sa.Column(
                "status",
                sa.String(length=64),
                nullable=False,
                server_default="CREATED",
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
            sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_projects_creator_id", "projects", ["creator_id"], unique=False)
        op.create_index(
            "ix_projects_creator_id_updated_at",
            "projects",
            ["creator_id", "updated_at"],
            unique=False,
        )
        op.create_index(
            "idx_projects_creator_id_created_at",
            "projects",
            ["creator_id", "created_at"],
            unique=False,
        )
    else:
        cols = _column_names(inspector, "projects")
        if "idea" not in cols:
            op.add_column("projects", sa.Column("idea", sa.Text(), nullable=True))
        if "workflow_type" not in cols:
            op.add_column(
                "projects",
                sa.Column(
                    "workflow_type",
                    sa.String(length=32),
                    nullable=False,
                    server_default="beginner",
                ),
            )
        if "status" not in cols:
            op.add_column(
                "projects",
                sa.Column(
                    "status",
                    sa.String(length=64),
                    nullable=False,
                    server_default="CREATED",
                ),
            )
        indexes = _index_names(inspector, "projects")
        if "ix_projects_creator_id_updated_at" not in indexes:
            op.create_index(
                "ix_projects_creator_id_updated_at",
                "projects",
                ["creator_id", "updated_at"],
                unique=False,
            )

    # Refresh inspector after possible project changes
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "chats" not in tables:
        op.create_table(
            "chats",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column(
                "title",
                sa.String(length=200),
                nullable=False,
                server_default="New Chat",
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
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chats_project_id", "chats", ["project_id"], unique=False)
        op.create_index(
            "ix_chats_project_id_updated_at",
            "chats",
            ["project_id", "updated_at"],
            unique=False,
        )

    if "chat_messages" not in tables:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("chat_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column(
                "metadata",
                sa.JSON()
                .with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_chat_id", "chat_messages", ["chat_id"], unique=False)
        op.create_index(
            "ix_chat_messages_created_at", "chat_messages", ["created_at"], unique=False
        )
        op.create_index(
            "ix_chat_messages_chat_id_created_at",
            "chat_messages",
            ["chat_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_messages" in tables:
        op.drop_index("ix_chat_messages_chat_id_created_at", table_name="chat_messages")
        op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
        op.drop_index("ix_chat_messages_chat_id", table_name="chat_messages")
        op.drop_table("chat_messages")

    if "chats" in tables:
        op.drop_index("ix_chats_project_id_updated_at", table_name="chats")
        op.drop_index("ix_chats_project_id", table_name="chats")
        op.drop_table("chats")

    # Only drop MindSurve-added columns; never drop shared Unilever `projects`.
    if "projects" in tables:
        cols = _column_names(inspector, "projects")
        indexes = _index_names(inspector, "projects")
        if "ix_projects_creator_id_updated_at" in indexes:
            op.drop_index("ix_projects_creator_id_updated_at", table_name="projects")
        if "status" in cols:
            op.drop_column("projects", "status")
        if "workflow_type" in cols:
            op.drop_column("projects", "workflow_type")
        if "idea" in cols:
            op.drop_column("projects", "idea")
