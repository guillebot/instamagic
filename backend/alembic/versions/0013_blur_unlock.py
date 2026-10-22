"""Persist blur-unlock verification for DO_NOT_POST images."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0013_blur_unlock"
down_revision = "0012_carousel_posts"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _columns("images")
    if "blur_unlock_status" not in cols:
        op.add_column(
            "images",
            sa.Column("blur_unlock_status", sa.String(length=16), nullable=False, server_default="none"),
        )
    if "blur_unlock_verified_at" not in cols:
        op.add_column("images", sa.Column("blur_unlock_verified_at", sa.DateTime(timezone=True), nullable=True))
    if "blur_unlock_details_json" not in cols:
        op.add_column("images", sa.Column("blur_unlock_details_json", sa.Text(), nullable=True))
    if "blur_unlock_geometry_hash" not in cols:
        op.add_column("images", sa.Column("blur_unlock_geometry_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    cols = _columns("images")
    if "blur_unlock_geometry_hash" in cols:
        op.drop_column("images", "blur_unlock_geometry_hash")
    if "blur_unlock_details_json" in cols:
        op.drop_column("images", "blur_unlock_details_json")
    if "blur_unlock_verified_at" in cols:
        op.drop_column("images", "blur_unlock_verified_at")
    if "blur_unlock_status" in cols:
        op.drop_column("images", "blur_unlock_status")
