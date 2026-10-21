"""Store file mtime on images so rescans can skip unchanged files without hashing.

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_image_mtime"
down_revision = "0003_scan_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("images")}
    if "file_mtime_ns" not in existing:
        op.add_column("images", sa.Column("file_mtime_ns", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("images")}
    if "file_mtime_ns" in existing:
        op.drop_column("images", "file_mtime_ns")
