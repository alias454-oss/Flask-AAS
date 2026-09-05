"""Generalize password capabilities for user authentication state.

Revision ID: 6c1f4d2a9b70
Revises: 29cf3dc887a0
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


revision = "6c1f4d2a9b70"
down_revision = "29cf3dc887a0"
branch_labels = None
depends_on = None


_OLD_INDEXES = (
    ("ix_password_reset_tokens_consumed_at", ["consumed_at"], False),
    ("ix_password_reset_tokens_expires_at", ["expires_at"], False),
    ("ix_password_reset_tokens_revoked_at", ["revoked_at"], False),
    ("ix_password_reset_tokens_token_hash", ["token_hash"], True),
    ("ix_password_reset_tokens_user_id", ["user_id"], False),
)

_NEW_INDEXES = (
    ("ix_user_auth_tokens_consumed_at", ["consumed_at"], False),
    ("ix_user_auth_tokens_expires_at", ["expires_at"], False),
    ("ix_user_auth_tokens_revoked_at", ["revoked_at"], False),
    ("ix_user_auth_tokens_token_hash", ["token_hash"], True),
    ("ix_user_auth_tokens_user_id", ["user_id"], False),
)


def upgrade():
    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        for name, _, _ in _OLD_INDEXES:
            batch_op.drop_index(name)

    op.rename_table("password_reset_tokens", "user_auth_tokens")

    with op.batch_alter_table("user_auth_tokens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("auth_version", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "failed_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        for name, columns, unique in _NEW_INDEXES:
            batch_op.create_index(name, columns, unique=unique)

    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "mfa_failed_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("user_sessions", schema=None) as batch_op:
        batch_op.drop_column("mfa_failed_attempts")

    # MFA-login challenges have no representation in the previous
    # password-reset-only schema. Discard them before removing the marker.
    op.execute(
        sa.text("DELETE FROM user_auth_tokens WHERE auth_version IS NOT NULL")
    )

    with op.batch_alter_table("user_auth_tokens", schema=None) as batch_op:
        for name, _, _ in _NEW_INDEXES:
            batch_op.drop_index(name)
        batch_op.drop_column("failed_attempts")
        batch_op.drop_column("auth_version")

    op.rename_table("user_auth_tokens", "password_reset_tokens")

    with op.batch_alter_table("password_reset_tokens", schema=None) as batch_op:
        for name, columns, unique in _OLD_INDEXES:
            batch_op.create_index(name, columns, unique=unique)
