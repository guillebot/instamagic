from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AttestIn(BaseModel):
    confirmed: bool = False
    statement: str = ""


class LibraryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    root_path: str = Field(min_length=1, max_length=1024)
    confirmed: bool = False
    statement: str = ""


class RatingIn(BaseModel):
    rating: str

    def normalized(self) -> str:
        allowed = {"reject", "review", "safe", "excellent"}
        if self.rating not in allowed:
            raise ValueError("Rating must be reject, review, safe, or excellent.")
        return self.rating


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class CaptionIn(BaseModel):
    confirmed: bool = False


class CaptionUpdate(BaseModel):
    status: str
    text: str | None = None


class QueueIn(BaseModel):
    image_id: UUID


class CropOut(BaseModel):
    id: UUID
    x: int
    y: int
    width: int
    height: int
    aspect_ratio: str
    reason: str
    contains_face: bool
    excludes_unsafe: bool
    approved: bool
    preview_url: str | None


class CropApproveIn(BaseModel):
    """Optional adjusted window in image pixel space. Omit all fields to approve as suggested."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None

    def adjusted(self) -> tuple[int, int, int, int] | None:
        values = (self.x, self.y, self.width, self.height)
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ValueError("Crop adjustments require x, y, width, and height together.")
        assert self.x is not None and self.y is not None and self.width is not None and self.height is not None
        return self.x, self.y, self.width, self.height


class BlurOut(BaseModel):
    id: UUID
    x: int
    y: int
    width: int
    height: int
    shape: str
    feather: int
    radius: float
    strength: str
    reason: str
    covers_unsafe: bool
    approved: bool
    preview_url: str | None


class BlurCreateIn(BaseModel):
    x: int
    y: int
    width: int
    height: int
    strength: str = "standard"
    reason: str = "Manual ellipse cover."


class BlurStrengthIn(BaseModel):
    strength: str


class ImageOut(BaseModel):
    id: UUID
    filename: str
    relative_path: str
    width: int | None
    height: int | None
    orientation: str | None
    captured_at: datetime | None
    camera: str | None
    lens: str | None
    focal_length_mm: float | None
    iso: int | None
    shutter: str | None
    aperture: float | None
    has_gps: bool
    session_name: str | None
    model_state: str | None
    human_rating: str | None
    effective_state: str
    aesthetic_score: float | None
    queued: bool
    queue_state: str | None
    duplicate_count: int
    has_approved_crop: bool = False
    thumbnail_url: str | None
    preview_url: str | None


class ImagePage(BaseModel):
    items: list[ImageOut]
    next_cursor: str | None


class CandidateOut(BaseModel):
    image: ImageOut
    score: int
    reasons: list[str]
    held_back: int
    crop_aspect: str | None
    model_state: str | None


class JobOut(BaseModel):
    id: UUID
    kind: str
    status: str
    detail: str | None = None
    error: str | None = None
    library_id: UUID | None = None
    files_seen: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    files_total: int | None = None
    current_path: str | None = None
    current_stage: str | None = None
    classified_count: int = 0
    pending_count: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FolderCrumb(BaseModel):
    name: str
    path: str


class FolderEntry(BaseModel):
    name: str
    path: str
    image_count: int
    thumbnail_url: str | None = None


class FolderListing(BaseModel):
    path: str
    parent: str | None
    breadcrumbs: list[FolderCrumb]
    folders: list[FolderEntry]
    image_count: int
    source: str


class QueueJobOut(BaseModel):
    id: str
    func_name: str | None = None
    library_id: str | None = None
    job_id: str | None = None
    status: str


class QueueOut(BaseModel):
    name: str
    queued: int = 0
    started: int = 0
    failed: int = 0
    workers: int = 0
    jobs: list[QueueJobOut] = Field(default_factory=list)
    available: bool = True
    detail: str | None = None


class QueuesOut(BaseModel):
    available: bool
    detail: str | None = None
    queues: list[QueueOut] = Field(default_factory=list)
