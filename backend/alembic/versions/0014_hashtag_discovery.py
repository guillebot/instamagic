"""Hashtag research budget tracking, cache, and related-account business discovery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014_hashtag_discovery"
down_revision = "0013_instagram_insights"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "hashtag_research_events" not in tables:
        op.create_table(
            "hashtag_research_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("hashtag", sa.String(128), nullable=False, index=True),
            sa.Column("hashtag_id", sa.String(64), nullable=True),
            sa.Column("action", sa.String(32), nullable=False, server_default="search"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        )
    if "hashtag_research_cache" not in tables:
        op.create_table(
            "hashtag_research_cache",
            sa.Column("hashtag", sa.String(128), primary_key=True),
            sa.Column("hashtag_id", sa.String(64), nullable=False),
            sa.Column("top_media_json", sa.Text(), nullable=True),
            sa.Column("recent_media_json", sa.Text(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "related_account_discovery" not in tables:
        op.create_table(
            "related_account_discovery",
            sa.Column("related_account_id", sa.Uuid(), sa.ForeignKey("related_accounts.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("followers_count", sa.Integer(), nullable=True),
            sa.Column("media_count", sa.Integer(), nullable=True),
            sa.Column("media_json", sa.Text(), nullable=True),
            sa.Column("common_hashtags_json", sa.Text(), nullable=True),
            sa.Column("avg_likes", sa.Float(), nullable=True),
            sa.Column("posts_per_week", sa.Float(), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    for name in ("related_account_discovery", "hashtag_research_cache", "hashtag_research_events"):
        if name in tables:
            op.drop_table(name)
