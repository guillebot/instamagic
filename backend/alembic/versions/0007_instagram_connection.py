"""Store sealed Instagram Graph credentials + cached account profile.

Idempotent against databases created from current metadata.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_instagram_connection"
down_revision = "0006_curation"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "instagram_connections" not in tables:
        op.create_table(
            "instagram_connections",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("access_token_ciphertext", sa.Text(), nullable=False, server_default=""),
            sa.Column("business_account_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("account_json", sa.Text(), nullable=True),
            sa.Column("account_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    if "instagram_connections" in tables:
        op.drop_table("instagram_connections")
