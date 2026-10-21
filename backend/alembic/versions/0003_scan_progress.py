"""Add scan progress columns on processing_jobs.

Idempotent so a database created from current metadata does not fail when the
columns already exist.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_scan_progress"
down_revision = "0002_blur_covers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("processing_jobs")}
    additions = [
        sa.Column("files_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_total", sa.Integer(), nullable=True),
        sa.Column("current_path", sa.String(length=1024), nullable=True),
        sa.Column("current_stage", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in additions:
        if column.name not in existing:
            op.add_column("processing_jobs", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in inspect(bind).get_columns("processing_jobs")}
    for name in (
        "started_at",
        "current_stage",
        "current_path",
        "files_total",
        "files_failed",
        "files_skipped",
        "files_processed",
        "files_seen",
    ):
        if name in existing:
            op.drop_column("processing_jobs", name)
