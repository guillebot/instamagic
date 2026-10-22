"""Per-image Gemini labeling status and description."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_gemini_label_status"
down_revision = "0009_instagram_facebook_page_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("images", sa.Column("gemini_label_status", sa.String(length=64), nullable=True))
    op.add_column("images", sa.Column("gemini_label_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "images",
        sa.Column("gemini_label_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("images", sa.Column("gemini_description", sa.Text(), nullable=True))
    op.add_column("images", sa.Column("gemini_mood", sa.String(length=128), nullable=True))
    op.create_index("ix_images_gemini_label_status", "images", ["gemini_label_status"])


def downgrade() -> None:
    op.drop_index("ix_images_gemini_label_status", table_name="images")
    op.drop_column("images", "gemini_mood")
    op.drop_column("images", "gemini_description")
    op.drop_column("images", "gemini_label_attempts")
    op.drop_column("images", "gemini_label_at")
    op.drop_column("images", "gemini_label_status")
