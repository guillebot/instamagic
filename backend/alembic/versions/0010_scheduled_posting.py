"""Scheduled posting: queue schedule fields, config singleton, publish attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_scheduled_posting"
down_revision = "0009_instagram_facebook_page_id"
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
        if "schedule_mode" not in cols:
            op.add_column("publishing_queue_items", sa.Column("schedule_mode", sa.String(16), nullable=True))
            op.create_index("ix_publishing_queue_items_schedule_mode", "publishing_queue_items", ["schedule_mode"])
        if "scheduled_at" not in cols:
            op.add_column("publishing_queue_items", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
            op.create_index("ix_publishing_queue_items_scheduled_at", "publishing_queue_items", ["scheduled_at"])
        if "publish_retry_after" not in cols:
            op.add_column(
                "publishing_queue_items",
                sa.Column("publish_retry_after", sa.DateTime(timezone=True), nullable=True),
            )

    if "posting_schedule_configs" not in tables:
        op.create_table(
            "posting_schedule_configs",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Buenos_Aires"),
            sa.Column("weekday_slots_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("max_posts_per_day", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("min_gap_hours", sa.Float(), nullable=False, server_default="4"),
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "publish_attempts" not in tables:
        op.create_table(
            "publish_attempts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "queue_item_id",
                sa.Uuid(),
                sa.ForeignKey("publishing_queue_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error", sa.Text(), nullable=True),
        )
        op.create_index("ix_publish_attempts_queue_item_id", "publish_attempts", ["queue_item_id"])


def downgrade() -> None:
    tables = _tables()
    if "publish_attempts" in tables:
        op.drop_table("publish_attempts")
    if "posting_schedule_configs" in tables:
        op.drop_table("posting_schedule_configs")
    if "publishing_queue_items" in tables:
        cols = _columns("publishing_queue_items")
        if "publish_retry_after" in cols:
            op.drop_column("publishing_queue_items", "publish_retry_after")
        if "scheduled_at" in cols:
            op.drop_index("ix_publishing_queue_items_scheduled_at", table_name="publishing_queue_items")
            op.drop_column("publishing_queue_items", "scheduled_at")
        if "schedule_mode" in cols:
            op.drop_index("ix_publishing_queue_items_schedule_mode", table_name="publishing_queue_items")
            op.drop_column("publishing_queue_items", "schedule_mode")
