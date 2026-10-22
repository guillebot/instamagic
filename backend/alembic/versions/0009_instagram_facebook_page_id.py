"""Store linked Facebook Page id for Page access tokens (OAuth).

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_instagram_facebook_page_id"
down_revision = "0008_meta_app_credentials"
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
    if "facebook_page_id" not in cols:
        op.add_column(
            "instagram_connections",
            sa.Column("facebook_page_id", sa.String(64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    tables = _tables()
    if "instagram_connections" not in tables:
        return
    cols = _columns("instagram_connections")
    if "facebook_page_id" in cols:
        op.drop_column("instagram_connections", "facebook_page_id")
