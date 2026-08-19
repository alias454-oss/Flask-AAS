"""Initial test plugin schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_example_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("greeting", sa.String(length=255), nullable=False),
        sa.Column("managed_secret", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "plugin_example_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("plugin_example_items")
    op.drop_table("plugin_example_settings")
