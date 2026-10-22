"""Comment inbox for operator replies (never auto-posted)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013_comment_inbox"
down_revision = "0012_hashtag_discovery"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "instagram_inbox_comments" not in tables:
        op.create_table(
            "instagram_inbox_comments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("ig_comment_id", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("ig_media_id", sa.String(64), nullable=False, index=True),
            sa.Column("parent_ig_comment_id", sa.String(64), nullable=True, index=True),
            sa.Column("post_id", sa.Uuid(), sa.ForeignKey("instagram_posts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("author_username", sa.String(64), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("commented_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("suggested_reply", sa.Text(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="open", index=True),
            sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    if "instagram_inbox_comments" in tables:
        op.drop_table("instagram_inbox_comments")
