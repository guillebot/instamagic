"""Enrichment prefetch for unrated, classifier-clean REVIEW photos: toggle and separate daily cap."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_enrichment_prefetch"
down_revision = "0022_enrichment"
branch_labels = None
depends_on = None

CONFIG_COLUMNS = (
    (
        "enrichment_prefetch_enabled",
        lambda: sa.Column("enrichment_prefetch_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    ),
    (
        "enrichment_prefetch_daily_cap",
        lambda: sa.Column("enrichment_prefetch_daily_cap", sa.Integer(), nullable=False, server_default="300"),
    ),
)


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("posting_schedule_configs")
    for name, factory in CONFIG_COLUMNS:
        if name not in existing:
            op.add_column("posting_schedule_configs", factory())


def downgrade() -> None:
    existing = _columns("posting_schedule_configs")
    for name, _factory in CONFIG_COLUMNS:
        if name in existing:
            op.drop_column("posting_schedule_configs", name)
