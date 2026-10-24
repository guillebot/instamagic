"""Enqueue recurring maintenance jobs (insights sync)."""

from __future__ import annotations

from datetime import timedelta

from instamagic.services.queues import QUEUE_SOCIAL
from redis import Redis
from rq import Queue

INSIGHTS_BOOTSTRAP_KEY = "instamagic:periodic:insights_bootstrap"
COMMENTS_BOOTSTRAP_KEY = "instamagic:periodic:comments_bootstrap"


def ensure_insights_sync_scheduled(redis_url: str) -> None:
    connection = Redis.from_url(redis_url)
    if connection.get(INSIGHTS_BOOTSTRAP_KEY):
        return
    queue = Queue(QUEUE_SOCIAL, connection=connection)
    queue.enqueue("instamagic.workers.tasks.sync_instagram_insights_job")
    connection.set(INSIGHTS_BOOTSTRAP_KEY, "1", ex=int(timedelta(hours=5).total_seconds()))


def ensure_comments_sync_scheduled(redis_url: str) -> None:
    connection = Redis.from_url(redis_url)
    if connection.get(COMMENTS_BOOTSTRAP_KEY):
        return
    queue = Queue(QUEUE_SOCIAL, connection=connection)
    queue.enqueue("instamagic.workers.tasks.sync_instagram_comments_job")
    connection.set(COMMENTS_BOOTSTRAP_KEY, "1", ex=int(timedelta(hours=1).total_seconds()))
