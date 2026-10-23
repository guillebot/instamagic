"""Background enrichment: stored hashtags per image and enrichment settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_enrichment"
down_revision = "0021_curation_skipped"
branch_labels = None
depends_on = None

IMAGE_COLUMNS = (
    ("enrich_hashtags_json", lambda: sa.Column("enrich_hashtags_json", sa.Text(), nullable=True)),
    ("enrich_status", lambda: sa.Column("enrich_status", sa.String(64), nullable=True)),
    (
        "enrich_attempts",
        lambda: sa.Column("enrich_attempts", sa.Integer(), nullable=False, server_default="0"),
    ),
    ("enrich_at", lambda: sa.Column("enrich_at", sa.DateTime(timezone=True), nullable=True)),
)
CONFIG_COLUMNS = (
    (
        "enrichment_enabled",
        lambda: sa.Column("enrichment_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    ),
    (
        "enrichment_paused",
        lambda: sa.Column("enrichment_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    ),
    (
        "enrichment_daily_cap",
        lambda: sa.Column("enrichment_daily_cap", sa.Integer(), nullable=False, server_default="400"),
    ),
)


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("images")
    for name, factory in IMAGE_COLUMNS:
        if name not in existing:
            op.add_column("images", factory())
    existing = _columns("posting_schedule_configs")
    for name, factory in CONFIG_COLUMNS:
        if name not in existing:
            op.add_column("posting_schedule_configs", factory())


def downgrade() -> None:
    existing = _columns("posting_schedule_configs")
    for name, _factory in CONFIG_COLUMNS:
        if name in existing:
            op.drop_column("posting_schedule_configs", name)
    existing = _columns("images")
    for name, _factory in IMAGE_COLUMNS:
        if name in existing:
            op.drop_column("images", name)
