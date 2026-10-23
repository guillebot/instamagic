"""Curation batches: per-run explanation of safe-rated images that were not picked."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_curation_skipped"
down_revision = "0020_blur_coverage_settings"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "skipped_json" not in _columns("curation_batches"):
        op.add_column("curation_batches", sa.Column("skipped_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if "skipped_json" in _columns("curation_batches"):
        op.drop_column("curation_batches", "skipped_json")
