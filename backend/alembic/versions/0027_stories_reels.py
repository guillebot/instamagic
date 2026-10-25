"""Standalone Stories and Reels: reel render columns + separate story daily cap."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_stories_reels"
down_revision = "0026_blur_detector_overrules"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


_QUEUE_COLUMNS = (
    ("reel_status", lambda: sa.Column("reel_status", sa.String(16), nullable=False, server_default="none")),
    ("reel_relative_path", lambda: sa.Column("reel_relative_path", sa.String(512), nullable=True)),
    ("reel_sha256", lambda: sa.Column("reel_sha256", sa.String(64), nullable=True)),
    ("reel_source_json", lambda: sa.Column("reel_source_json", sa.Text(), nullable=True)),
    ("reel_error", lambda: sa.Column("reel_error", sa.Text(), nullable=True)),
    ("reel_duration_s", lambda: sa.Column("reel_duration_s", sa.Float(), nullable=True)),
    ("reel_rendered_at", lambda: sa.Column("reel_rendered_at", sa.DateTime(timezone=True), nullable=True)),
)


def upgrade() -> None:
    queue_cols = _columns("publishing_queue_items")
    for name, factory in _QUEUE_COLUMNS:
        if name not in queue_cols:
            op.add_column("publishing_queue_items", factory())
    if "max_stories_per_day" not in _columns("posting_schedule_configs"):
        op.add_column(
            "posting_schedule_configs",
            sa.Column("max_stories_per_day", sa.Integer(), nullable=False, server_default="2"),
        )


def downgrade() -> None:
    if "max_stories_per_day" in _columns("posting_schedule_configs"):
        op.drop_column("posting_schedule_configs", "max_stories_per_day")
    queue_cols = _columns("publishing_queue_items")
    for name, _factory in reversed(_QUEUE_COLUMNS):
        if name in queue_cols:
            op.drop_column("publishing_queue_items", name)
