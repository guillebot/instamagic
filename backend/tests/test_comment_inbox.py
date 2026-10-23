from __future__ import annotations

from datetime import UTC, datetime

from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.models import InstagramPost
from instamagic.services.instagram.client import FakeInstagramClient
from instamagic.services.instagram.comment_inbox import sync_inbox_comments


def test_sync_inbox_comments(app_env) -> None:
    del app_env
    session = session_factory()()
    settings = get_settings()
    post = InstagramPost(
        ig_media_id="fake-media-001",
        caption="Test",
        published_at=datetime.now(UTC),
        status="published",
    )
    session.add(post)
    session.commit()
    client = FakeInstagramClient()
    # Fake permissions include manage_comments via fetch_granted_permissions
    from instamagic.models import InstagramConnection

    conn = InstagramConnection(id="default", token_scopes_json='["instagram_manage_comments"]')
    session.merge(conn)
    session.commit()
    result = sync_inbox_comments(session, settings, client, force=True)
    assert result["ok"] is True
    assert result["synced"] >= 1
    session.close()
