"""Growth features: collaborators per queue item (+ per-shoot memory), Reel slot, hashtag pools."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_growth_features"
down_revision = "0027_stories_reels"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


_QUEUE_COLUMNS = (
    ("collaborators_json", lambda: sa.Column("collaborators_json", sa.Text(), nullable=True)),
    ("reel_for_item_id", lambda: sa.Column("reel_for_item_id", sa.Uuid(), nullable=True)),
)
_CONFIG_COLUMNS = (
    (
        "reel_slot_enabled",
        lambda: sa.Column("reel_slot_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    ),
    (
        "reel_slot_time",
        lambda: sa.Column("reel_slot_time", sa.String(8), nullable=False, server_default="11:00"),
    ),
    ("hashtag_pools_json", lambda: sa.Column("hashtag_pools_json", sa.Text(), nullable=True)),
)


def upgrade() -> None:
    queue_cols = _columns("publishing_queue_items")
    for name, factory in _QUEUE_COLUMNS:
        if name not in queue_cols:
            op.add_column("publishing_queue_items", factory())
    if "ix_publishing_queue_items_reel_for_item_id" not in {
        ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("publishing_queue_items")
    }:
        op.create_index(
            "ix_publishing_queue_items_reel_for_item_id", "publishing_queue_items", ["reel_for_item_id"]
        )
    config_cols = _columns("posting_schedule_configs")
    for name, factory in _CONFIG_COLUMNS:
        if name not in config_cols:
            op.add_column("posting_schedule_configs", factory())
    if "shoot_collaborators" not in _tables():
        op.create_table(
            "shoot_collaborators",
            sa.Column("shoot_key", sa.String(512), primary_key=True),
            sa.Column("collaborators_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    if "shoot_collaborators" in _tables():
        op.drop_table("shoot_collaborators")
    config_cols = _columns("posting_schedule_configs")
    for name, _ in _CONFIG_COLUMNS:
        if name in config_cols:
            op.drop_column("posting_schedule_configs", name)
    queue_cols = _columns("publishing_queue_items")
    if "reel_for_item_id" in queue_cols:
        op.drop_index("ix_publishing_queue_items_reel_for_item_id", table_name="publishing_queue_items")
    for name, _ in _QUEUE_COLUMNS:
        if name in queue_cols:
            op.drop_column("publishing_queue_items", name)
