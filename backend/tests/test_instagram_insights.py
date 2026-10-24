"""Insights sync and performance ranking with mocked Graph responses."""

from __future__ import annotations

import pytest
from instamagic.config import get_settings
from instamagic.db import session_factory
from instamagic.services.instagram.client import GraphInstagramClient
from instamagic.services.instagram.insights_sync import sync_instagram_insights
from instamagic.services.instagram.permissions import FetchedScopes
from instamagic.services.recommendations.performance import best_posting_times
from instamagic.services.recommendations.rank import CandidateInput, rank_candidates
from sqlalchemy import select


def test_rank_includes_performance_when_weighted() -> None:
    base = CandidateInput(
        "a", 0.5, "safe", 0.5, None, True, None, "REVIEW", 0.5, 0.5, performance_score=0.9
    )
    other = CandidateInput(
        "b", 0.5, "safe", 0.5, None, True, None, "REVIEW", 0.5, 0.5, performance_score=0.1
    )
    without = rank_candidates([base, other], limit=2, performance_weight=0.0)
    with_perf = rank_candidates([base, other], limit=2, performance_weight=0.25)
    assert without[0].image_id == with_perf[0].image_id == "a"
    assert with_perf[0].score >= without[0].score


def test_sync_insights_persists_account_snapshot(monkeypatch: pytest.MonkeyPatch, app_env) -> None:
    del app_env
    settings = get_settings()
    session = session_factory()()

    def fake_permissions(self):
        return FetchedScopes(
            scopes=frozenset({"instagram_manage_insights", "instagram_basic"}),
            determined=True,
        )

    def fake_account_insights(self):
        return {
            "follower_count": 500,
            "reach": 100,
            "profile_views": 20,
            "impressions": None,
            "accounts_engaged": 5,
        }

    def fake_media(self, *, limit: int = 50):
        del limit
        return []

    monkeypatch.setattr(
        GraphInstagramClient, "fetch_granted_permissions_detailed", fake_permissions
    )
    monkeypatch.setattr(
        GraphInstagramClient, "fetch_account_insight_snapshot", fake_account_insights
    )
    monkeypatch.setattr(GraphInstagramClient, "list_recent_media", fake_media)

    client = GraphInstagramClient(access_token="tok", business_account_id="178414000")
    result = sync_instagram_insights(session, settings, client, force=True)
    assert result["ok"] is True
    from instamagic.models import InstagramAccountInsightSnapshot

    snap = session.scalar(select(InstagramAccountInsightSnapshot))
    assert snap is not None
    assert snap.follower_count == 500
    session.close()


def test_best_posting_times_defaults(app_env) -> None:
    del app_env
    session = session_factory()()
    payload = best_posting_times(session)
    assert payload["best_hours_utc"]
    assert payload["source"] in {"defaults", "history"}
    session.close()


def test_sync_requires_insights_permission(monkeypatch: pytest.MonkeyPatch, app_env) -> None:
    del app_env
    settings = get_settings()
    session = session_factory()()

    def fake_permissions(self):
        return FetchedScopes(scopes=frozenset({"instagram_basic"}), determined=True)

    monkeypatch.setattr(
        GraphInstagramClient, "fetch_granted_permissions_detailed", fake_permissions
    )
    client = GraphInstagramClient(access_token="tok", business_account_id="178414000")
    result = sync_instagram_insights(session, settings, client, force=True)
    assert result["ok"] is False
    assert result["error"] == "insights_permission_missing"
    session.close()
