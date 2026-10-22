"""Account + media insight snapshots and connection scope cache."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_instagram_insights"
down_revision = "0009_instagram_facebook_page_id"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    conn_cols = _columns("instagram_connections") if "instagram_connections" in tables else set()
    if "instagram_connections" in tables:
        if "token_scopes_json" not in conn_cols:
            op.add_column("instagram_connections", sa.Column("token_scopes_json", sa.Text(), nullable=True))
        if "insights_last_sync_at" not in conn_cols:
            op.add_column(
                "instagram_connections",
                sa.Column("insights_last_sync_at", sa.DateTime(timezone=True), nullable=True),
            )

    if "instagram_account_insight_snapshots" not in tables:
        op.create_table(
            "instagram_account_insight_snapshots",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
            sa.Column("follower_count", sa.Integer(), nullable=True),
            sa.Column("reach", sa.Integer(), nullable=True),
            sa.Column("profile_views", sa.Integer(), nullable=True),
            sa.Column("impressions", sa.Integer(), nullable=True),
            sa.Column("accounts_engaged", sa.Integer(), nullable=True),
        )

    if "instagram_metrics" in tables:
        metric_cols = _columns("instagram_metrics")
        if "views" not in metric_cols:
            op.add_column("instagram_metrics", sa.Column("views", sa.Integer(), nullable=True))
        if "total_interactions" not in metric_cols:
            op.add_column("instagram_metrics", sa.Column("total_interactions", sa.Integer(), nullable=True))


def downgrade() -> None:
    tables = _tables()
    if "instagram_account_insight_snapshots" in tables:
        op.drop_table("instagram_account_insight_snapshots")
    if "instagram_connections" in tables:
        conn_cols = _columns("instagram_connections")
        for name in ("insights_last_sync_at", "token_scopes_json"):
            if name in conn_cols:
                op.drop_column("instagram_connections", name)
    if "instagram_metrics" in tables:
        metric_cols = _columns("instagram_metrics")
        for name in ("total_interactions", "views"):
            if name in metric_cols:
                op.drop_column("instagram_metrics", name)
