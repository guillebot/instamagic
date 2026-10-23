"""Per-cover blur edge mode: hard (uniform interior) vs soft (feathered)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0019_blur_edge_mode"
down_revision = "0018_story_publish"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _columns("blur_covers")
    if "edge_mode" not in cols:
        op.add_column(
            "blur_covers",
            sa.Column("edge_mode", sa.String(length=8), nullable=False, server_default="soft"),
        )


def downgrade() -> None:
    cols = _columns("blur_covers")
    if "edge_mode" in cols:
        op.drop_column("blur_covers", "edge_mode")
