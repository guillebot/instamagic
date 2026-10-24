"""Read-only "why not picked" explanation for a curation run.

Lists human safe/excellent images in the batch scope that did not become picks, with one plain
reason each. Purely descriptive: it never changes ratings, safety states, blur status or gates.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from instamagic.logging_config import get_logger
from instamagic.models import CurationBatch, HumanRating, Image, ImageRating, SafetyState
from instamagic.services.images.folders import escape_like, normalize_folder
from instamagic.services.pipeline import is_attested
from instamagic.services.recommendations.gates import gemini_gate
from instamagic.services.recommendations.rank import _hamming
from instamagic.services.recommendations.refusals import is_gemini_refused
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

log = get_logger(__name__)

MAX_SKIPPED_ITEMS = 500
NEAR_DUPLICATE_HAMMING = 10  # Same threshold rank_candidates uses.

CoveringBlurFn = Callable[[Session, Image], bool]


def _latest_rating_times(session: Session, image_ids: Iterable[Any]) -> dict[str, datetime]:
    ids = list(image_ids)
    if not ids:
        return {}
    out: dict[str, datetime] = {}
    for start in range(0, len(ids), 900):
        chunk = ids[start : start + 900]
        rows = session.execute(
            select(ImageRating.image_id, func.max(ImageRating.created_at))
            .where(ImageRating.image_id.in_(chunk))
            .group_by(ImageRating.image_id)
        ).all()
        out.update({str(image_id): created for image_id, created in rows if created is not None})
    return out


def latest_rating_times(session: Session, image_ids: Iterable[Any]) -> dict[str, datetime]:
    return _latest_rating_times(session, image_ids)


def _pct(ratio: float) -> int:
    return int(ratio * 100)  # floor, so 94.9% never reads as 95%


def _do_not_post_reason(session: Session, image: Image) -> tuple[str, str, dict[str, Any]]:
    """(code, reason, extra) for a DO_NOT_POST image whose blur unlock is not effective."""
    from instamagic.services.blurs.coverage_report import blur_coverage_report

    if not image.blurs:
        return "dnp_no_blur", "DO_NOT_POST, no blur covers yet (add them in the blur editor)", {}
    try:
        report = blur_coverage_report(session, image)
    except Exception:  # noqa: BLE001 - explanation must never break a run
        log.warning("curation_skip_coverage_report_failed", extra={"image_id": str(image.id)})
        report = {}
    threshold = report.get("coverage_threshold_percent")
    regions = report.get("regions") or []
    uncovered = [row for row in regions if not row.get("covered")]
    extra: dict[str, Any] = {"coverage_threshold_percent": threshold}
    if uncovered:
        worst = min(float(row.get("coverage") or 0.0) for row in uncovered)
        extra["coverage_percent"] = _pct(worst)
        extra["uncovered_regions"] = len(uncovered)
        label = f"{len(uncovered)} region" + ("s" if len(uncovered) != 1 else "")
        return (
            "dnp_coverage",
            f"blur coverage {_pct(worst)}% < {threshold}% threshold on {label} (fix in editor)",
            extra,
        )
    verification = report.get("verification") or {}
    status = verification.get("status") or image.blur_unlock_status or "none"
    detections = verification.get("classifier_detections") or []
    if verification.get("recheck") == "fail" and detections:
        labels = sorted(
            {str(d.get("label", d)) if isinstance(d, dict) else str(d) for d in detections}
        )
        return (
            "dnp_recheck",
            f"DO_NOT_POST, the re-check still detects {', '.join(labels[:4])} on the blurred render (fix in editor)",
            extra,
        )
    if status == "verified":
        return (
            "dnp_stale",
            "DO_NOT_POST, blur verification is no longer current (covers or threshold changed); re-verify in editor",
            extra,
        )
    if status == "failed":
        return "dnp_failed", "DO_NOT_POST, blur verification failed (open the blur editor)", extra
    return (
        "dnp_unverified",
        "DO_NOT_POST, blur not verified (approve covers in the blur editor)",
        extra,
    )


def _ineligible_reason(
    session: Session,
    image: Image,
    *,
    attested: dict[Any, bool],
    covering_blur_fn: CoveringBlurFn,
) -> tuple[str, str, dict[str, Any]]:
    from instamagic.services.blurs.gate_context import publishing_gate_kwargs
    from instamagic.services.blurs.unlock import blur_unlock_effective

    if image.skip_reason:
        return "scan_skipped", f"skipped at scan: {image.skip_reason}", {}
    if is_gemini_refused(image):
        return (
            "gemini_refused",
            "Gemini refused this photo once; it is never sent again (review it manually)",
            {},
        )
    if image.library_id not in attested:
        attested[image.library_id] = is_attested(session, image.library_id)
    if not attested[image.library_id]:
        return "not_attested", "library not attested (confirm it in Settings)", {}
    if image.model_state == SafetyState.DO_NOT_POST.value and not blur_unlock_effective(
        session, image
    ):
        return _do_not_post_reason(session, image)
    approved = next((crop for crop in image.crops if crop.approved), None)
    if (
        image.model_state == SafetyState.SAFE_AFTER_CROP.value
        and approved is None
        and not covering_blur_fn(session, image)
    ):
        return "needs_crop", "SAFE_AFTER_CROP, needs an approved crop or covering blur", {}
    gate = gemini_gate(
        attested=True,
        operator_confirmed=True,
        policy="text_only",
        **publishing_gate_kwargs(session, image),
    )
    if not gate.allowed:
        return "gate", "; ".join(gate.reasons) or "blocked by the publishing gate", {}
    return "unknown", "not eligible at run time (state changed since?)", {}


def _queue_note(image: Image) -> str | None:
    states = sorted(
        {item.state for item in (image.queue_items or []) if getattr(item, "state", None)}
    )
    return f"already in queue ({', '.join(states)})" if states else None


def build_skipped_report(
    session: Session,
    batch: CurationBatch,
    *,
    eligible_ids: Iterable[str],
    shortlist_ids: Iterable[str],
    picked_ids: Iterable[str],
    blocked_ids: Iterable[str] = (),
    covering_blur_fn: CoveringBlurFn,
    image_ids: list[Any] | None = None,
) -> dict[str, Any]:
    eligible = set(eligible_ids)
    shortlist = list(shortlist_ids)
    shortlist_set = set(shortlist)
    picked = set(picked_ids)
    blocked = set(blocked_ids)

    stmt = (
        select(Image)
        .where(
            func.lower(Image.human_rating).in_(
                [HumanRating.SAFE.value, HumanRating.EXCELLENT.value]
            )
        )
        .options(
            selectinload(Image.crops),
            selectinload(Image.blurs),
            selectinload(Image.queue_items),
        )
    )
    if batch.library_id is not None:
        stmt = stmt.where(Image.library_id == batch.library_id)
    if image_ids:
        stmt = stmt.where(Image.id.in_(image_ids))
    if batch.folder:
        normalized = normalize_folder(batch.folder)
        if normalized:
            stmt = stmt.where(Image.relative_path.like(escape_like(f"{normalized}/%"), escape="\\"))
    rows = session.scalars(stmt).all()
    by_id = {str(image.id): image for image in rows}
    rated_at = _latest_rating_times(session, [image.id for image in rows])

    previous = session.scalar(
        select(CurationBatch.created_at)
        .where(CurationBatch.created_at < batch.created_at)
        .where(CurationBatch.id != batch.id)
        .order_by(CurationBatch.created_at.desc())
        .limit(1)
    )

    def _is_new(image_id: str) -> bool:
        when = rated_at.get(image_id)
        if previous is None or when is None:
            return False
        return _aware(when) > _aware(previous)

    attested: dict[Any, bool] = {}
    items: list[dict[str, Any]] = []
    for image_id, image in by_id.items():
        if image_id in picked:
            continue
        extra: dict[str, Any] = {}
        dup_of: str | None = None
        if image_id in eligible:
            if image_id in blocked:
                code, reason = "gemini_blocked", "Gemini declined this image (safety block)"
            elif image_id in shortlist_set:
                code, reason = "not_returned", "shortlisted, but not returned by Gemini"
            else:
                for other in shortlist:
                    other_image = by_id.get(other)
                    distance = _hamming(image.phash, other_image.phash if other_image else None)
                    if distance is not None and distance <= NEAR_DUPLICATE_HAMMING:
                        dup_of = other
                        break
                if dup_of:
                    code, reason = "near_duplicate", f"near-duplicate of {dup_of[:8]}"
                else:
                    code, reason = "limit", f"below score cutoff (batch size limit {batch.limit})"
        else:
            code, reason, extra = _ineligible_reason(
                session, image, attested=attested, covering_blur_fn=covering_blur_fn
            )
        when = rated_at.get(image_id)
        items.append(
            {
                "image_id": image_id,
                "code": code,
                "reason": reason,
                "rating": (image.human_rating or "").lower() or None,
                "model_state": image.model_state,
                "rated_at": when.isoformat() if when else None,
                "since_last_run": _is_new(image_id),
                "duplicate_of": dup_of,
                "queue_note": _queue_note(image),
                "blur_editor": code.startswith("dnp_") or code == "needs_crop",
                **extra,
            }
        )
    items.sort(key=lambda row: row["rated_at"] or "", reverse=True)
    new_picked = sum(1 for image_id in picked if _is_new(image_id))
    new_skipped = sum(1 for row in items if row["since_last_run"])
    return {
        "since": previous.isoformat() if previous else None,
        "since_rated": new_picked + new_skipped,
        "since_picked": new_picked,
        "since_skipped": new_skipped,
        "total": len(items),
        "truncated": len(items) > MAX_SKIPPED_ITEMS,
        "items": items[:MAX_SKIPPED_ITEMS],
    }


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def record_skipped(session: Session, batch: CurationBatch, **kwargs: Any) -> None:
    """Store the explanation on the batch; failures are logged, never fatal to the run."""
    try:
        report = build_skipped_report(session, batch, **kwargs)
        batch.skipped_json = json.dumps(report, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        log.exception("curation_skip_report_failed", extra={"batch_id": str(batch.id)})


def load_skipped(batch: CurationBatch) -> dict[str, Any] | None:
    if not batch.skipped_json:
        return None
    try:
        data = json.loads(batch.skipped_json)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
