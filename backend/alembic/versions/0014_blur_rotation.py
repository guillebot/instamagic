"""Rotation and rounded-rectangle support on blur covers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0014_blur_rotation"
down_revision = "0013_blur_unlock"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _columns("blur_covers")
    if "rotation_deg" not in cols:
        op.add_column(
            "blur_covers",
            sa.Column("rotation_deg", sa.Float(), nullable=False, server_default="0"),
        )
    if "corner_radius" not in cols:
        op.add_column(
            "blur_covers",
            sa.Column("corner_radius", sa.Float(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    cols = _columns("blur_covers")
    if "corner_radius" in cols:
        op.drop_column("blur_covers", "corner_radius")
    if "rotation_deg" in cols:
        op.drop_column("blur_covers", "rotation_deg")
