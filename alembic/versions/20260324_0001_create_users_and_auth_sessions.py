"""Shared-DB safe: users (if missing) + auth_sessions

Revision ID: 20260324_0001
Revises:
Create Date: 2026-03-24

On Unilever staging, `users` already exists — this migration skips it and only
creates MindSurve `auth_sessions`. Uses alembic_version_mindsurve (see env.py).

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260324_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "users" not in tables:
        # Unilever-compatible shape (for empty local DBs only)
        op.create_table(
            "users",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("phone", sa.String(length=20), nullable=True),
            sa.Column("date_of_birth", sa.DateTime(), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "is_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "dashboard_onboarding_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "dashboard_onboarding_skipped",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "create_study_onboarding_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "create_study_onboarding_skipped",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
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
            sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
            sa.Column("password_reset_token", sa.String(length=255), nullable=True),
            sa.Column("password_reset_expires", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=True),
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
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"
            ),
        )
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
        op.create_index(
            "ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"], unique=False
        )
        op.create_index(
            "ix_auth_sessions_revoked_at", "auth_sessions", ["revoked_at"], unique=False
        )
        op.create_index(
            "ix_auth_sessions_refresh_token_hash",
            "auth_sessions",
            ["refresh_token_hash"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "auth_sessions" in tables:
        op.drop_index("ix_auth_sessions_refresh_token_hash", table_name="auth_sessions")
        op.drop_index("ix_auth_sessions_revoked_at", table_name="auth_sessions")
        op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
        op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
        op.drop_table("auth_sessions")

    # Never drop shared `users` on downgrade — Unilever owns that table in staging.
