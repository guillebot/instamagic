"""Separate Reel schedule (own slots + daily cap + share_to_feed), Reel music library.

Also restores feed photos/carousels that the 0028 Reel slot left unscheduled (approved, no time,
no schedule mode, with a Reel version pointing at them): they go back to auto-slot so the next
fill puts them in a feed photo slot again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_reel_schedule_music"
down_revision = "0028_growth_features"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


_CONFIG_COLUMNS = (
    (
        "reel_slots_json",
        lambda: sa.Column("reel_slots_json", sa.Text(), nullable=False, server_default='["15:00", "21:00"]'),
    ),
    ("reels_per_day", lambda: sa.Column("reels_per_day", sa.Integer(), nullable=False, server_default="2")),
    (
        "reel_share_to_feed",
        lambda: sa.Column("reel_share_to_feed", sa.Boolean(), nullable=False, server_default=sa.true()),
    ),
    (
        "music_pick_mode",
        lambda: sa.Column("music_pick_mode", sa.String(16), nullable=False, server_default="rotate"),
    ),
)
_QUEUE_COLUMNS = (
    ("music_choice", lambda: sa.Column("music_choice", sa.String(64), nullable=True)),
    ("reel_auto", lambda: sa.Column("reel_auto", sa.Boolean(), nullable=False, server_default=sa.false())),
)


def upgrade() -> None:
    config_cols = _columns("posting_schedule_configs")
    for name, factory in _CONFIG_COLUMNS:
        if name not in config_cols:
            op.add_column("posting_schedule_configs", factory())
    queue_cols = _columns("publishing_queue_items")
    for name, factory in _QUEUE_COLUMNS:
        if name not in queue_cols:
            op.add_column("publishing_queue_items", factory())
    if "music_tracks" not in _tables():
        op.create_table(
            "music_tracks",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("relative_path", sa.String(512), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_s", sa.Float(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    # Undo the 0028 "Reel slot took the photo's slot" behaviour: re-queue those photos for feed slots.
    op.execute(
        sa.text(
            "UPDATE publishing_queue_items SET schedule_mode = 'auto_slot' "
            "WHERE state = 'APPROVED' AND scheduled_at IS NULL AND schedule_mode IS NULL "
            "AND media_type IN ('IMAGE', 'CAROUSEL') AND id IN ("
            "SELECT reel_for_item_id FROM publishing_queue_items WHERE reel_for_item_id IS NOT NULL)"
        )
    )


def downgrade() -> None:
    if "music_tracks" in _tables():
        op.drop_table("music_tracks")
    queue_cols = _columns("publishing_queue_items")
    for name, _ in _QUEUE_COLUMNS:
        if name in queue_cols:
            op.drop_column("publishing_queue_items", name)
    config_cols = _columns("posting_schedule_configs")
    for name, _ in _CONFIG_COLUMNS:
        if name in config_cols:
            op.drop_column("posting_schedule_configs", name)
