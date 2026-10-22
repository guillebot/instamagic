"""Phase 2: publish queue fields, related accounts, comment drafts.

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_phase2_publish"
down_revision = "0004_image_mtime"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    queue_cols = _columns("publishing_queue_items") if "publishing_queue_items" in tables else set()
    if "caption_id" not in queue_cols:
        op.add_column(
            "publishing_queue_items",
            sa.Column("caption_id", sa.Uuid(), sa.ForeignKey("captions.id", ondelete="SET NULL"), nullable=True),
        )
    if "published_at" not in queue_cols:
        op.add_column("publishing_queue_items", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    if "last_error" not in queue_cols:
        op.add_column("publishing_queue_items", sa.Column("last_error", sa.Text(), nullable=True))

    post_cols = _columns("instagram_posts") if "instagram_posts" in tables else set()
    if "queue_item_id" not in post_cols:
        op.add_column(
            "instagram_posts",
            sa.Column(
                "queue_item_id",
                sa.Uuid(),
                sa.ForeignKey("publishing_queue_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "ig_container_id" not in post_cols:
        op.add_column("instagram_posts", sa.Column("ig_container_id", sa.String(64), nullable=True))
    if "status" not in post_cols:
        op.add_column(
            "instagram_posts",
            sa.Column("status", sa.String(32), nullable=False, server_default="published"),
        )

    if "related_accounts" not in tables:
        op.create_table(
            "related_accounts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("handle", sa.String(64), nullable=False, unique=True),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("profile_url", sa.String(512), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("why_relevant", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "comment_drafts" not in tables:
        op.create_table(
            "comment_drafts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.id", ondelete="SET NULL"), nullable=True),
            sa.Column(
                "related_account_id",
                sa.Uuid(),
                sa.ForeignKey("related_accounts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("target_context", sa.Text(), nullable=False, server_default=""),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    if "comment_drafts" in tables:
        op.drop_table("comment_drafts")
    if "related_accounts" in tables:
        op.drop_table("related_accounts")
    post_cols = _columns("instagram_posts") if "instagram_posts" in tables else set()
    for name in ("status", "ig_container_id", "queue_item_id"):
        if name in post_cols:
            op.drop_column("instagram_posts", name)
    queue_cols = _columns("publishing_queue_items") if "publishing_queue_items" in tables else set()
    for name in ("last_error", "published_at", "caption_id"):
        if name in queue_cols:
            op.drop_column("publishing_queue_items", name)
