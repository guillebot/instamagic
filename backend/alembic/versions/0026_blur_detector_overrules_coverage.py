"""Detector pass on the blurred render can overrule the coverage gate (default on)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_blur_detector_overrules"
down_revision = "0025_engage"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table)}


def upgrade() -> None:
    if "blur_detector_overrules_coverage" not in _columns("posting_schedule_configs"):
        op.add_column(
            "posting_schedule_configs",
            sa.Column("blur_detector_overrules_coverage", sa.Boolean(), nullable=False, server_default="true"),
        )


def downgrade() -> None:
    if "blur_detector_overrules_coverage" in _columns("posting_schedule_configs"):
        op.drop_column("posting_schedule_configs", "blur_detector_overrules_coverage")
