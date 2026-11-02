"""Royalty-free music sources: licence/provenance columns on music_tracks + auto-refill settings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_music_sources"
down_revision = "0029_reel_schedule_music"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


_TRACK_COLUMNS = (
    ("artist", lambda: sa.Column("artist", sa.String(200), nullable=True)),
    ("source", lambda: sa.Column("source", sa.String(32), nullable=True)),
    ("source_id", lambda: sa.Column("source_id", sa.String(64), nullable=True)),
    ("source_url", lambda: sa.Column("source_url", sa.Text(), nullable=True)),
    ("license", lambda: sa.Column("license", sa.String(16), nullable=True)),
    ("license_version", lambda: sa.Column("license_version", sa.String(8), nullable=True)),
    ("license_url", lambda: sa.Column("license_url", sa.Text(), nullable=True)),
    ("attribution", lambda: sa.Column("attribution", sa.Text(), nullable=True)),
    ("bit_rate", lambda: sa.Column("bit_rate", sa.Integer(), nullable=True)),
    ("downloaded_at", lambda: sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True)),
)

_CONFIG_COLUMNS = (
    (
        "music_autorefill_enabled",
        lambda: sa.Column("music_autorefill_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    ),
    (
        "music_autorefill_target",
        lambda: sa.Column("music_autorefill_target", sa.Integer(), nullable=False, server_default="15"),
    ),
    ("music_saved_queries_json", lambda: sa.Column("music_saved_queries_json", sa.Text(), nullable=True)),
    (
        "music_autorefill_last_at",
        lambda: sa.Column("music_autorefill_last_at", sa.DateTime(timezone=True), nullable=True),
    ),
)


def upgrade() -> None:
    have = _columns("music_tracks")
    for name, make in _TRACK_COLUMNS:
        if name not in have:
            op.add_column("music_tracks", make())
    indexes = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("music_tracks")}
    if "ix_music_tracks_source_id" not in indexes:
        op.create_index("ix_music_tracks_source_id", "music_tracks", ["source_id"])
    have = _columns("posting_schedule_configs")
    for name, make in _CONFIG_COLUMNS:
        if name not in have:
            op.add_column("posting_schedule_configs", make())


def downgrade() -> None:
    have = _columns("posting_schedule_configs")
    for name, _ in reversed(_CONFIG_COLUMNS):
        if name in have:
            op.drop_column("posting_schedule_configs", name)
    indexes = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("music_tracks")}
    if "ix_music_tracks_source_id" in indexes:
        op.drop_index("ix_music_tracks_source_id", table_name="music_tracks")
    have = _columns("music_tracks")
    for name, _ in reversed(_TRACK_COLUMNS):
        if name in have:
            op.drop_column("music_tracks", name)
