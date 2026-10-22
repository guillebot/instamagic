from __future__ import annotations

from instamagic.db import session_factory
from instamagic.services.instagram.client import FakeInstagramClient
from instamagic.services.instagram.hashtag_research import budget_status, research_hashtag


def test_hashtag_research_records_budget(app_env) -> None:
    del app_env
    session = session_factory()()
    client = FakeInstagramClient()
    result = research_hashtag(session, client, "berlin", force=True)
    assert result["hashtag"] == "berlin"
    budget = budget_status(session)
    assert budget["used_unique_hashtags_7d"] >= 1
    session.close()
