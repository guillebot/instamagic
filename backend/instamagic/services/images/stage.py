"""Photo lifecycle stage — the single source of truth for the operator-facing stage pill.

Stages (first match wins):

- ``posted``     — a queue item for the photo is PUBLISHED (detail: permalink when known).
- ``scheduled``  — a queue item is APPROVED / PUBLISHING / FAILED
                   (detail: ``slot``, ``awaiting_slot``, ``publishing`` or ``failed``).
- ``rejected``   — human rated it reject.
- ``candidate``  — rated safe/excellent and, when blur verification is required (model flagged it
                   or it has blur covers), the covers are verified. Not yet approved for publish.
                   Detail: ``caption_ready`` / ``needs_caption``; ``in_queue`` when already a queue
                   CANDIDATE awaiting approval.
- ``to_review``  — needs a decision: rated review; or rated safe/excellent but blur not verified;
                   or unrated with blur covers present (verification pending or failed).
- ``new``        — everything else: scanned, unrated, no human action.

``photo_stage`` is pure (Python objects); ``stage_clause`` is the SQL mirror used for filters and
counts. The SQL mirror treats ``blur_unlock_status == 'verified'`` as verified; the Python version
additionally checks the verification still matches the current covers (same gate as publishing).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from instamagic.models.enums import CaptionStatus, HumanRating, QueueState, SafetyState
from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.sql.elements import ColumnElement

STAGES: tuple[str, ...] = ("new", "to_review", "candidate", "scheduled", "posted", "rejected")

STAGE_LABELS: dict[str, str] = {
    "new": "New",
    "to_review": "To review",
    "candidate": "Candidate",
    "scheduled": "Scheduled",
    "posted": "Posted",
    "rejected": "Rejected",
}

_RATED_OK = frozenset({HumanRating.SAFE.value, HumanRating.EXCELLENT.value})
_SCHEDULED_STATES = frozenset(
    {QueueState.APPROVED.value, QueueState.PUBLISHING.value, QueueState.FAILED.value}
)


@dataclass(frozen=True)
class QueueFact:
    state: str
    scheduled_at: object | None = None


@dataclass(frozen=True)
class StageResult:
    stage: str
    detail: str | None = None
    caption_ready: bool | None = None
    permalink: str | None = None

    @property
    def label(self) -> str:
        return STAGE_LABELS[self.stage]


def photo_stage(
    *,
    human_rating: str | None,
    model_state: str | None,
    has_blurs: bool,
    blur_verified: bool,
    queue: Iterable[QueueFact] = (),
    caption_statuses: Iterable[str] = (),
    permalink: str | None = None,
) -> StageResult:
    items = list(queue)
    if any(item.state == QueueState.PUBLISHED.value for item in items):
        return StageResult("posted", "posted", permalink=permalink)
    scheduled = [item for item in items if item.state in _SCHEDULED_STATES]
    if scheduled:
        states = {item.state for item in scheduled}
        if QueueState.PUBLISHING.value in states:
            detail = "publishing"
        elif any(item.state == QueueState.APPROVED.value and item.scheduled_at for item in scheduled):
            detail = "slot"
        elif QueueState.APPROVED.value in states:
            detail = "awaiting_slot"
        else:
            detail = "failed"
        return StageResult("scheduled", detail)
    if human_rating == HumanRating.REJECT.value:
        return StageResult("rejected")
    needs_verification = model_state == SafetyState.DO_NOT_POST.value or has_blurs
    if human_rating in _RATED_OK and (not needs_verification or blur_verified):
        caption_ready = any(status != CaptionStatus.REJECTED.value for status in caption_statuses)
        if any(item.state == QueueState.CANDIDATE.value for item in items):
            detail = "in_queue"
        else:
            detail = "caption_ready" if caption_ready else "needs_caption"
        return StageResult("candidate", detail, caption_ready=caption_ready)
    if human_rating == HumanRating.REVIEW.value:
        return StageResult("to_review", "rated_review")
    if human_rating in _RATED_OK:
        return StageResult("to_review", "blur_unverified")
    if has_blurs:
        return StageResult("to_review", "blur_pending")
    return StageResult("new")


def queue_item_stage(state: str, scheduled_at: object | None, image: dict) -> dict[str, object]:
    """Stage of one queue item (a photo can be posted once and queued again)."""
    if state == QueueState.PUBLISHED.value:
        stage, detail = "posted", "posted"
    elif state in _SCHEDULED_STATES:
        result = photo_stage(
            human_rating=None,
            model_state=None,
            has_blurs=False,
            blur_verified=False,
            queue=[QueueFact(state, scheduled_at)],
        )
        stage, detail = result.stage, result.detail
    elif image.get("stage") in {"candidate", "to_review", "rejected"}:
        stage, detail = str(image["stage"]), image.get("stage_detail")
    else:
        stage, detail = "candidate", "in_queue"
    return {"stage": stage, "stage_label": STAGE_LABELS[stage], "stage_detail": detail}


def stage_clause(stage: str, image_model, queue_model, blur_model) -> ColumnElement[bool]:  # type: ignore[no-untyped-def]
    """SQL WHERE clause selecting images in ``stage`` (mirror of :func:`photo_stage`)."""
    img = image_model
    posted = exists(
        select(queue_model.id).where(
            queue_model.image_id == img.id, queue_model.state == QueueState.PUBLISHED.value
        )
    )
    scheduled = exists(
        select(queue_model.id).where(
            queue_model.image_id == img.id, queue_model.state.in_(sorted(_SCHEDULED_STATES))
        )
    )
    has_blurs = exists(select(blur_model.id).where(blur_model.image_id == img.id))
    rated_ok = img.human_rating.in_(sorted(_RATED_OK))
    needs_verification = or_(img.model_state == SafetyState.DO_NOT_POST.value, has_blurs)
    verified = img.blur_unlock_status == "verified"
    open_ = and_(not_(posted), not_(scheduled))
    not_rejected = or_(img.human_rating.is_(None), img.human_rating != HumanRating.REJECT.value)
    candidate = and_(rated_ok, or_(not_(needs_verification), verified))
    if stage == "posted":
        return posted
    if stage == "scheduled":
        return and_(not_(posted), scheduled)
    if stage == "rejected":
        return and_(open_, img.human_rating == HumanRating.REJECT.value)
    if stage == "candidate":
        return and_(open_, candidate)
    if stage == "to_review":
        return and_(
            open_,
            not_rejected,
            not_(candidate),
            or_(img.human_rating.in_([HumanRating.REVIEW.value, *sorted(_RATED_OK)]), has_blurs),
        )
    if stage == "new":
        return and_(open_, img.human_rating.is_(None), not_(has_blurs))
    raise ValueError(f"unknown stage {stage!r}")
