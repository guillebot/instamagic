"""Daily Engage list: settings singleton, runs, and manual-engagement targets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0025_engage"
down_revision = "0024_gemini_refusals"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "engage_settings" not in tables:
        op.create_table(
            "engage_settings",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("daily_target", sa.Integer(), nullable=False, server_default="18"),
            sa.Column("account_cooldown_days", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("run_hour_local", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("hashtags_per_run", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("seed_hashtags_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "engage_runs" not in tables:
        op.create_table(
            "engage_runs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("run_date", sa.String(10), nullable=False, index=True),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="schedule"),
            sa.Column("status", sa.String(16), nullable=False, server_default="running", index=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("targets_created", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "engage_targets" not in tables:
        op.create_table(
            "engage_targets",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("run_id", sa.Uuid(), sa.ForeignKey("engage_runs.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("run_date", sa.String(10), nullable=False, index=True),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("source_value", sa.String(128), nullable=False),
            sa.Column("ig_media_id", sa.String(64), nullable=True, index=True),
            sa.Column("permalink", sa.String(512), nullable=False, unique=True),
            sa.Column("media_url", sa.Text(), nullable=True),
            sa.Column("media_type", sa.String(32), nullable=True),
            sa.Column("username", sa.String(64), nullable=True, index=True),
            sa.Column("caption", sa.Text(), nullable=True),
            sa.Column("like_count", sa.Integer(), nullable=True),
            sa.Column("comments_count", sa.Integer(), nullable=True),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("suggestion", sa.Text(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="open", index=True),
            sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    for name in ("engage_targets", "engage_runs", "engage_settings"):
        if name in tables:
            op.drop_table(name)
