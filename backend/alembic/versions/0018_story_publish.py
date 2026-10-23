"""Optional Instagram Story publishing alongside feed posts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0018_story_publish"
down_revision = "0017_blur_rotation"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    queue_cols = _columns("publishing_queue_items")
    if "story_share_enabled" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_share_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "story_render_mode" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_render_mode", sa.String(length=32), nullable=True),
        )
    if "story_border_color" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_border_color", sa.String(length=16), nullable=True),
        )
    if "story_timing_mode" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column(
                "story_timing_mode",
                sa.String(length=16),
                nullable=False,
                server_default="with_post",
            ),
        )
    if "story_delay_hours" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_delay_hours", sa.Integer(), nullable=True),
        )
    if "story_scheduled_at" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "story_published_at" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_published_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "story_last_error" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("story_last_error", sa.Text(), nullable=True),
        )
    if "feed_publish_relative_path" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("feed_publish_relative_path", sa.String(length=512), nullable=True),
        )

    attempt_cols = _columns("publish_attempts")
    if "kind" not in attempt_cols:
        op.add_column(
            "publish_attempts",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="feed"),
        )
    if "ig_media_id" not in attempt_cols:
        op.add_column(
            "publish_attempts",
            sa.Column("ig_media_id", sa.String(length=64), nullable=True),
        )
    if "ig_container_id" not in attempt_cols:
        op.add_column(
            "publish_attempts",
            sa.Column("ig_container_id", sa.String(length=64), nullable=True),
        )

    schedule_cols = _columns("posting_schedule_configs")
    if "story_share_default" not in schedule_cols:
        op.add_column(
            "posting_schedule_configs",
            sa.Column("story_share_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "story_render_mode_default" not in schedule_cols:
        op.add_column(
            "posting_schedule_configs",
            sa.Column(
                "story_render_mode_default",
                sa.String(length=32),
                nullable=False,
                server_default="blur_backdrop",
            ),
        )
    if "story_border_color_default" not in schedule_cols:
        op.add_column(
            "posting_schedule_configs",
            sa.Column(
                "story_border_color_default",
                sa.String(length=16),
                nullable=False,
                server_default="black",
            ),
        )


def downgrade() -> None:
    schedule_cols = _columns("posting_schedule_configs")
    if "story_border_color_default" in schedule_cols:
        op.drop_column("posting_schedule_configs", "story_border_color_default")
    if "story_render_mode_default" in schedule_cols:
        op.drop_column("posting_schedule_configs", "story_render_mode_default")
    if "story_share_default" in schedule_cols:
        op.drop_column("posting_schedule_configs", "story_share_default")

    attempt_cols = _columns("publish_attempts")
    if "ig_container_id" in attempt_cols:
        op.drop_column("publish_attempts", "ig_container_id")
    if "ig_media_id" in attempt_cols:
        op.drop_column("publish_attempts", "ig_media_id")
    if "kind" in attempt_cols:
        op.drop_column("publish_attempts", "kind")

    queue_cols = _columns("publishing_queue_items")
    for name in (
        "feed_publish_relative_path",
        "story_last_error",
        "story_published_at",
        "story_scheduled_at",
        "story_delay_hours",
        "story_timing_mode",
        "story_border_color",
        "story_render_mode",
        "story_share_enabled",
    ):
        if name in queue_cols:
            op.drop_column("publishing_queue_items", name)
