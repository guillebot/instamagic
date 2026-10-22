from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("name")
    @classmethod
    def clean_tag(cls, value: str) -> str:
        text = value.strip().lower()
        if not text or any(ch in text for ch in "/\\\x00\n\r\t"):
            raise ValueError("Tag name is not valid.")
        if ".." in text or text.startswith("."):
            raise ValueError("Tag name is not valid.")
        return text[:64]


class CaptionIn(BaseModel):
    confirmed: bool = False


class CaptionUpdate(BaseModel):
    status: str
    text: str | None = None


class QueueIn(BaseModel):
    image_id: UUID


class PublishIn(BaseModel):
    caption_id: UUID


class RelatedAccountIn(BaseModel):
    handle: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=200)
    profile_url: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default=None, max_length=4000)
    why_relevant: str | None = Field(default=None, max_length=2000)

    @field_validator("handle")
    @classmethod
    def clean_handle(cls, value: str) -> str:
        text = value.strip().lstrip("@").lower()
        if not text or any(ch in text for ch in "/\\\x00\n\r\t "):
            raise ValueError("Handle is not valid.")
        if ".." in text:
            raise ValueError("Handle is not valid.")
        return text[:64]


class InstagramCredentialsIn(BaseModel):
    """Paste a long-lived Page access token + IG business user id (never logged)."""

    access_token: str = Field(min_length=20, max_length=4096)
    business_account_id: str = Field(min_length=1, max_length=64)

    @field_validator("access_token")
    @classmethod
    def clean_token(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ch in text for ch in "\x00\n\r"):
            raise ValueError("Access token is not valid.")
        return text

    @field_validator("business_account_id")
    @classmethod
    def clean_account_id(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ch in text for ch in "/\\\x00\n\r\t "):
            raise ValueError("Instagram business account id is not valid.")
        return text


class InstagramOAuthSelectIn(BaseModel):
    pending_token: str = Field(min_length=20, max_length=8192)
    page_id: str = Field(min_length=1, max_length=64)

    @field_validator("page_id")
    @classmethod
    def clean_page_id(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ch in text for ch in "/\\\x00\n\r\t "):
            raise ValueError("Facebook Page id is not valid.")
        return text


class MetaAppCredentialsIn(BaseModel):
    """Meta Developer App ID + App Secret (secret sealed; never logged)."""

    app_id: str = Field(min_length=1, max_length=64)
    app_secret: str = Field(min_length=8, max_length=512)

    @field_validator("app_id")
    @classmethod
    def clean_app_id(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ch in text for ch in "/\\\x00\n\r\t "):
            raise ValueError("Meta App ID is not valid.")
        return text

    @field_validator("app_secret")
    @classmethod
    def clean_app_secret(cls, value: str) -> str:
        text = value.strip()
        if not text or any(ch in text for ch in "\x00\n\r"):
            raise ValueError("Meta App Secret is not valid.")
        return text


class CommentDraftIn(BaseModel):
    image_id: UUID | None = None
    related_account_id: UUID | None = None
    target_context: str = Field(default="", max_length=2000)
    confirmed: bool = False


class CommentDraftUpdate(BaseModel):
    status: str
    text: str | None = None


class HashtagIn(BaseModel):
    confirmed: bool = False


class CurationIn(BaseModel):
    """limit null/0 = all eligible Safe/Publishable frames (chunked Gemini calls).

    include_images: per-run toggle. Env GEMINI_IMAGE_POLICY is a ceiling —
    only preview/safe_previews/selected may attach derivative JPEG bytes.
    """

    library_id: UUID | None = None
    folder: str | None = Field(default=None, max_length=1024)
    image_ids: list[UUID] | None = None
    limit: int | None = Field(default=12, ge=0, le=2000)
    include_images: bool | None = None
    confirmed: bool = False


class CurationReorderIn(BaseModel):
    pick_ids: list[UUID] = Field(min_length=1, max_length=500)


class CurationPickUpdate(BaseModel):
    status: str | None = None
    preferred_caption_id: UUID | None = None
    suggested_tags: list[str] | None = None
    caption_text: str | None = Field(default=None, max_length=2000)


class CurationQueueIn(BaseModel):
    pick_ids: list[UUID] = Field(min_length=1, max_length=500)


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


class BlurSuggestIn(BaseModel):
    strength: str = "standard"


class ImageOut(BaseModel):
    id: UUID
    filename: str
    relative_path: str
    width: int | None
    height: int | None
    orientation: str | None
    captured_at: datetime | None
    ingested_at: datetime | None = None
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
    has_approved_covering_blur: bool = False
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
