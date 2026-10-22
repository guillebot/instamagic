"""Gemini curation batches and picks (draft suggestions only).

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_curation"
down_revision = "0005_phase2_publish"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "curation_batches" not in tables:
        op.create_table(
            "curation_batches",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("library_id", sa.Uuid(), sa.ForeignKey("libraries.id", ondelete="CASCADE"), nullable=True),
            sa.Column("folder", sa.String(1024), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
            sa.Column("limit", sa.Integer(), nullable=False, server_default="12"),
            sa.Column("detail", sa.String(512), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_curation_batches_library_id", "curation_batches", ["library_id"])
        op.create_index("ix_curation_batches_status", "curation_batches", ["status"])

    tables = _tables()
    if "curation_picks" not in tables:
        op.create_table(
            "curation_picks",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("curation_batches.id", ondelete="CASCADE"), nullable=False),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("gemini_rank", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("local_score", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reasons_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("suggested_tags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(16), nullable=False, server_default="suggested"),
            sa.Column(
                "preferred_caption_id",
                sa.Uuid(),
                sa.ForeignKey("captions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("batch_id", "image_id", name="uq_curation_pick_batch_image"),
        )
        op.create_index("ix_curation_picks_batch_id", "curation_picks", ["batch_id"])
        op.create_index("ix_curation_picks_image_id", "curation_picks", ["image_id"])
        op.create_index("ix_curation_picks_sort_order", "curation_picks", ["sort_order"])
        op.create_index("ix_curation_picks_status", "curation_picks", ["status"])


def downgrade() -> None:
    tables = _tables()
    if "curation_picks" in tables:
        op.drop_table("curation_picks")
    if "curation_batches" in tables:
        op.drop_table("curation_batches")
