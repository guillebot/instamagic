"""Carousel queue items and per-slide image rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0012_carousel_posts"
down_revision = "0011_gemini_label_status"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "publishing_queue_items" in tables:
        cols = _columns("publishing_queue_items")
        if "media_type" not in cols:
            op.add_column(
                "publishing_queue_items",
                sa.Column("media_type", sa.String(16), nullable=False, server_default="IMAGE"),
            )

    if "publishing_queue_slides" not in tables:
        op.create_table(
            "publishing_queue_slides",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "queue_item_id",
                sa.Uuid(),
                sa.ForeignKey("publishing_queue_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
            sa.Column("crop_id", sa.Uuid(), sa.ForeignKey("crop_suggestions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("queue_item_id", "image_id", name="uq_queue_slide_image"),
        )
        op.create_index("ix_publishing_queue_slides_queue_item_id", "publishing_queue_slides", ["queue_item_id"])


def downgrade() -> None:
    tables = _tables()
    if "publishing_queue_slides" in tables:
        op.drop_table("publishing_queue_slides")
    if "publishing_queue_items" in tables:
        cols = _columns("publishing_queue_items")
        if "media_type" in cols:
            op.drop_column("publishing_queue_items", "media_type")
