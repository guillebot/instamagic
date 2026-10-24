"""Permanent Gemini-refusal flag on images and prefetch circuit-breaker reason on the config."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_gemini_refusals"
down_revision = "0023_enrichment_prefetch"
branch_labels = None
depends_on = None

IMAGE_COLUMNS = (
    ("gemini_refused_at", lambda: sa.Column("gemini_refused_at", sa.DateTime(timezone=True), nullable=True)),
    ("gemini_refused_reason", lambda: sa.Column("gemini_refused_reason", sa.String(64), nullable=True)),
)
CONFIG_COLUMNS = (
    ("enrichment_prefetch_paused_reason", lambda: sa.Column("enrichment_prefetch_paused_reason", sa.Text(), nullable=True)),
    (
        "enrichment_prefetch_paused_at",
        lambda: sa.Column("enrichment_prefetch_paused_at", sa.DateTime(timezone=True), nullable=True),
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
    # Backfill: photos Gemini already refused (label or hashtags) are permanently flagged.
    op.execute(
        sa.text(
        """
        UPDATE images
        SET gemini_refused_at = COALESCE(gemini_label_at, enrich_at, CURRENT_TIMESTAMP),
            gemini_refused_reason = LEFT(
                CASE WHEN COALESCE(gemini_label_status, '') LIKE 'blocked:%' THEN gemini_label_status
                     ELSE enrich_status END, 64)
        WHERE gemini_refused_at IS NULL
          AND (COALESCE(gemini_label_status, '') LIKE 'blocked:%' OR COALESCE(enrich_status, '') LIKE 'blocked:%')
        """
        )
    )


def downgrade() -> None:
    existing = _columns("posting_schedule_configs")
    for name, _factory in CONFIG_COLUMNS:
        if name in existing:
            op.drop_column("posting_schedule_configs", name)
    existing = _columns("images")
    for name, _factory in IMAGE_COLUMNS:
        if name in existing:
            op.drop_column("images", name)
