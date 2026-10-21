"""Add blur_covers for non-destructive region covers.

Idempotent against fresh installs where 0001's create_all already materializes
the current metadata (including blur_covers).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_blur_covers"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "blur_covers" in inspect(bind).get_table_names():
        return
    op.create_table(
        "blur_covers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("shape", sa.String(length=16), nullable=False, server_default="ellipse"),
        sa.Column("feather", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("radius", sa.Float(), nullable=False, server_default="20"),
        sa.Column("strength", sa.String(length=16), nullable=False, server_default="standard"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("covers_unsafe", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("derivative_path", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_blur_covers_image_id", "blur_covers", ["image_id"])


def downgrade() -> None:
    op.drop_index("ix_blur_covers_image_id", table_name="blur_covers")
    op.drop_table("blur_covers")
