"""Historical Instagram performance features for ranking and posting-time hints."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC
from statistics import mean
from uuid import UUID

from instamagic.models import ContentTag, ImageTag, InstagramMetrics, InstagramPost
from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_BEST_HOURS = (10, 14, 18)
DEFAULT_BEST_WEEKDAYS = (1, 3, 5)  # Tue, Thu, Sat (ISO weekday)


@dataclass(frozen=True)
class PerformanceSignals:
    by_image_id: dict[str, float]
    by_tag: dict[str, float]
    by_hour: dict[int, float]
    by_weekday: dict[int, float]


def _metric_score(metric: InstagramMetrics) -> float:
    if metric.engagement_rate is not None:
        return max(0.0, min(1.0, float(metric.engagement_rate)))
    parts = [metric.likes, metric.comments, metric.saves, metric.shares]
    total = sum(p or 0 for p in parts)
    if total <= 0:
        return 0.0
    return min(1.0, total / 100.0)


def load_performance_signals(session: Session) -> PerformanceSignals:
    rows = session.execute(
        select(InstagramPost, InstagramMetrics)
        .join(InstagramMetrics, InstagramMetrics.post_id == InstagramPost.id)
        .where(InstagramPost.image_id.is_not(None))
        .order_by(InstagramMetrics.captured_at.desc())
    ).all()
    by_image: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[int, list[float]] = defaultdict(list)
    by_weekday: dict[int, list[float]] = defaultdict(list)
    tag_scores: dict[str, list[float]] = defaultdict(list)
    seen_post: set[UUID] = set()
    for post, metric in rows:
        if post.id in seen_post:
            continue
        seen_post.add(post.id)
        score = _metric_score(metric)
        if post.image_id:
            by_image[str(post.image_id)].append(score)
        published = post.published_at
        if published is not None:
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            by_hour[published.hour].append(score)
            by_weekday[published.isoweekday()].append(score)
        if post.image_id:
            tag_rows = session.scalars(
                select(ContentTag.name)
                .join(ImageTag, ImageTag.tag_id == ContentTag.id)
                .where(ImageTag.image_id == post.image_id)
            ).all()
            for tag in tag_rows:
                tag_scores[tag.lower()].append(score)

    def avg_map(items: dict[str, list[float]]) -> dict[str, float]:
        return {key: sum(vals) / len(vals) for key, vals in items.items() if vals}

    def avg_int_map(items: dict[int, list[float]]) -> dict[int, float]:
        return {key: sum(vals) / len(vals) for key, vals in items.items() if vals}

    return PerformanceSignals(
        by_image_id={k: mean(v) for k, v in by_image.items()},
        by_tag=avg_map(tag_scores),
        by_hour=avg_int_map(by_hour),
        by_weekday=avg_int_map(by_weekday),
    )


def image_performance_boost(signals: PerformanceSignals, image_id: str, tags: list[str]) -> float:
    scores: list[float] = []
    if image_id in signals.by_image_id:
        scores.append(signals.by_image_id[image_id])
    for tag in tags:
        key = tag.lower()
        if key in signals.by_tag:
            scores.append(signals.by_tag[key])
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def best_posting_times(session: Session) -> dict[str, object]:
    signals = load_performance_signals(session)
    hours = sorted(signals.by_hour.items(), key=lambda pair: pair[1], reverse=True)
    weekdays = sorted(signals.by_weekday.items(), key=lambda pair: pair[1], reverse=True)
    if len(hours) < 3:
        best_hours = list(DEFAULT_BEST_HOURS)
        source = "defaults"
    else:
        best_hours = [hour for hour, _ in hours[:5]]
        source = "history"
    if len(weekdays) < 2:
        best_weekdays = list(DEFAULT_BEST_WEEKDAYS)
        weekday_source = "defaults"
    else:
        best_weekdays = [day for day, _ in weekdays[:4]]
        weekday_source = "history"
    return {
        "source": source,
        "weekday_source": weekday_source,
        "best_hours_utc": best_hours,
        "best_weekdays_iso": best_weekdays,
        "hour_scores": {str(h): round(s, 4) for h, s in signals.by_hour.items()},
        "weekday_scores": {str(d): round(s, 4) for d, s in signals.by_weekday.items()},
        "tag_scores": {k: round(v, 4) for k, v in sorted(signals.by_tag.items(), key=lambda p: -p[1])[:20]},
    }


def insights_dashboard(session: Session) -> dict[str, object]:
    from instamagic.models import InstagramAccountInsightSnapshot

    snaps = session.scalars(
        select(InstagramAccountInsightSnapshot).order_by(InstagramAccountInsightSnapshot.captured_at.asc()).limit(90)
    ).all()
    follower_trend = [
        {
            "captured_at": row.captured_at.isoformat(),
            "follower_count": row.follower_count,
            "reach": row.reach,
            "profile_views": row.profile_views,
        }
        for row in snaps
    ]
    top_posts = session.execute(
        select(InstagramPost, InstagramMetrics)
        .join(InstagramMetrics, InstagramMetrics.post_id == InstagramPost.id)
        .order_by(InstagramMetrics.engagement_rate.desc().nullslast(), InstagramMetrics.captured_at.desc())
        .limit(12)
    ).all()
    posts_out: list[dict[str, object]] = []
    for post, metric in top_posts:
        posts_out.append(
            {
                "post_id": str(post.id),
                "image_id": str(post.image_id) if post.image_id else None,
                "permalink": post.permalink,
                "caption": (post.caption or "")[:160],
                "published_at": post.published_at.isoformat() if post.published_at else None,
                "engagement_rate": metric.engagement_rate,
                "reach": metric.reach,
                "likes": metric.likes,
                "saves": metric.saves,
            }
        )
    timing = best_posting_times(session)
    return {
        "follower_trend": follower_trend,
        "top_posts": posts_out,
        "by_tag": timing["tag_scores"],
        "by_hour": timing["hour_scores"],
        "by_weekday": timing["weekday_scores"],
        "best_posting_times": {
            "best_hours_utc": timing["best_hours_utc"],
            "best_weekdays_iso": timing["best_weekdays_iso"],
            "source": timing["source"],
        },
    }
