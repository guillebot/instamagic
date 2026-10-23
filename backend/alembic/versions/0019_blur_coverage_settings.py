"""Blur coverage threshold and region padding on posting schedule config."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_blur_coverage_settings"
down_revision = "0018_story_publish"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("posting_schedule_configs")
    if "blur_coverage_threshold" not in cols:
        op.add_column(
            "posting_schedule_configs",
            sa.Column("blur_coverage_threshold", sa.Integer(), nullable=False, server_default="95"),
        )
    if "blur_region_padding_px" not in cols:
        op.add_column(
            "posting_schedule_configs",
            sa.Column("blur_region_padding_px", sa.Integer(), nullable=False, server_default="6"),
        )


def downgrade() -> None:
    cols = _columns("posting_schedule_configs")
    if "blur_region_padding_px" in cols:
        op.drop_column("posting_schedule_configs", "blur_region_padding_px")
    if "blur_coverage_threshold" in cols:
        op.drop_column("posting_schedule_configs", "blur_coverage_threshold")
