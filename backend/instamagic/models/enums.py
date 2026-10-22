from __future__ import annotations

import enum


class SafetyState(str, enum.Enum):
    SAFE = "SAFE"
    SAFE_AFTER_CROP = "SAFE_AFTER_CROP"
    REVIEW = "REVIEW"
    DO_NOT_POST = "DO_NOT_POST"


class HumanRating(str, enum.Enum):
    REJECT = "reject"
    REVIEW = "review"
    SAFE = "safe"
    EXCELLENT = "excellent"


class QueueState(str, enum.Enum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class JobKind(str, enum.Enum):
    SCAN = "scan"
    DERIVATIVE = "derivative"
    ASSESS = "assess"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class CaptionStatus(str, enum.Enum):
    GENERATED = "generated"
    USED = "used"
    MODIFIED = "modified"
    REJECTED = "rejected"


class TagSource(str, enum.Enum):
    HUMAN = "human"
    HEURISTIC = "heuristic"


class CommentDraftStatus(str, enum.Enum):
    DRAFT = "draft"
    COPIED = "copied"
    DISCARDED = "discarded"
