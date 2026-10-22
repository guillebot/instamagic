from __future__ import annotations

import enum


class SafetyState(enum.StrEnum):
    SAFE = "SAFE"
    SAFE_AFTER_CROP = "SAFE_AFTER_CROP"
    REVIEW = "REVIEW"
    DO_NOT_POST = "DO_NOT_POST"


class HumanRating(enum.StrEnum):
    REJECT = "reject"
    REVIEW = "review"
    SAFE = "safe"
    EXCELLENT = "excellent"


class QueueState(enum.StrEnum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class JobKind(enum.StrEnum):
    SCAN = "scan"
    DERIVATIVE = "derivative"
    ASSESS = "assess"
    CURATE = "curate"


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CaptionStatus(enum.StrEnum):
    GENERATED = "generated"
    USED = "used"
    MODIFIED = "modified"
    REJECTED = "rejected"


class TagSource(enum.StrEnum):
    HUMAN = "human"
    HEURISTIC = "heuristic"
    GEMINI = "gemini"


class CommentDraftStatus(enum.StrEnum):
    DRAFT = "draft"
    COPIED = "copied"
    DISCARDED = "discarded"


class CurationBatchStatus(enum.StrEnum):
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class CurationPickStatus(enum.StrEnum):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    QUEUED = "queued"
