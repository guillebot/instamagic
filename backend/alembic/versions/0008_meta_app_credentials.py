"""Seal Meta App ID/Secret on instagram_connections (DB-first OAuth).

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_meta_app_credentials"
down_revision = "0007_instagram_connection"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "instagram_connections" not in tables:
        return
    cols = _columns("instagram_connections")
    if "meta_app_id" not in cols:
        op.add_column(
            "instagram_connections",
            sa.Column("meta_app_id", sa.String(64), nullable=False, server_default=""),
        )
    if "meta_app_secret_ciphertext" not in cols:
        op.add_column(
            "instagram_connections",
            sa.Column("meta_app_secret_ciphertext", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    tables = _tables()
    if "instagram_connections" not in tables:
        return
    cols = _columns("instagram_connections")
    if "meta_app_secret_ciphertext" in cols:
        op.drop_column("instagram_connections", "meta_app_secret_ciphertext")
    if "meta_app_id" in cols:
        op.drop_column("instagram_connections", "meta_app_id")
