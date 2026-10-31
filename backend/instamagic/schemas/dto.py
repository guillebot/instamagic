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


class ManualCaptionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2200)


class CaptionUpdate(BaseModel):
    status: str
    text: str | None = None


class QueueIn(BaseModel):
    image_id: UUID
    story_share_enabled: bool | None = None
    story_render_mode: str | None = Field(default=None, max_length=32)
    story_border_color: str | None = Field(default=None, max_length=16)
    story_timing_mode: str | None = Field(default=None, max_length=16)
    story_delay_hours: int | None = Field(default=None, ge=1, le=12)


class QueueStoryUpdateIn(BaseModel):
    story_share_enabled: bool | None = None
    story_render_mode: str | None = Field(default=None, max_length=32)
    story_border_color: str | None = Field(default=None, max_length=16)
    story_timing_mode: str | None = Field(default=None, max_length=16)
    story_delay_hours: int | None = Field(default=None, ge=1, le=12)


class CarouselQueueIn(BaseModel):
    image_ids: list[UUID] = Field(min_length=2, max_length=10)


class MediaQueueIn(BaseModel):
    """Standalone Story (one item per photo) or Reel (1-10 photos in one video)."""

    image_ids: list[UUID] = Field(min_length=1, max_length=10)


class PostTypeIn(BaseModel):
    media_type: str = Field(min_length=3, max_length=16)


class CarouselReorderIn(BaseModel):
    image_ids: list[UUID] = Field(min_length=2, max_length=10)


class PublishIn(BaseModel):
    # Required for feed posts, carousels and reels; standalone Stories have no caption.
    caption_id: UUID | None = None


class ScheduleIn(BaseModel):
    mode: str = Field(description="explicit or auto_slot")
    scheduled_at: datetime | None = None
    caption_id: UUID | None = None


class ImagePublishReadinessOut(BaseModel):
    publishing_allowed: bool
    publishing_reasons: list[str]
    caption_generation_allowed: bool
    caption_generation_reasons: list[str]
    library_attested: bool
    queue_item_id: UUID | None = None
    queue_state: str | None = None
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    instagram_configured: bool
    instagram_target_username: str | None = None
    feed_preview_url: str | None = None
    uses_approved_crop: bool = False


class PostingScheduleConfigIn(BaseModel):
    timezone: str | None = Field(default=None, max_length=64)
    weekday_slots: dict[str, list[str]] | None = None
    max_posts_per_day: int | None = Field(default=None, ge=1, le=24)
    max_stories_per_day: int | None = Field(default=None, ge=1, le=10)
    min_gap_hours: float | None = Field(default=None, ge=0.5, le=48)
    paused: bool | None = None
    story_share_default: bool | None = None
    story_render_mode_default: str | None = Field(default=None, max_length=32)
    story_border_color_default: str | None = Field(default=None, max_length=16)
    blur_coverage_threshold: int | None = Field(default=None, ge=60, le=100)
    blur_region_padding_px: int | None = Field(default=None, ge=0, le=20)
    blur_detector_overrules_coverage: bool | None = None
    reel_slot_enabled: bool | None = None
    hashtag_pools: dict[str, list[str]] | None = None
    reel_slots: list[str] | None = Field(default=None, max_length=10)
    reels_per_day: int | None = Field(default=None, ge=1, le=10)
    reel_share_to_feed: bool | None = None
    music_pick_mode: str | None = Field(default=None, max_length=16)


class CollaboratorsIn(BaseModel):
    usernames: list[str] = Field(default_factory=list, max_length=10)


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
    # False: return hashtags pre-generated by background enrichment when present (no Gemini call).
    refresh: bool = False


class EnrichmentSettingsIn(BaseModel):
    enabled: bool | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=20000)
    prefetch_enabled: bool | None = None
    prefetch_daily_cap: int | None = Field(default=None, ge=0, le=20000)


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
    story_share_enabled: bool | None = None
    story_render_mode: str | None = Field(default=None, max_length=32)
    story_border_color: str | None = Field(default=None, max_length=16)
    story_timing_mode: str | None = Field(default=None, max_length=16)
    story_delay_hours: int | None = Field(default=None, ge=1, le=12)


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
    rotation_deg: float = 0.0
    corner_radius: float = 0.0
    feather: int
    edge_mode: str = "soft"
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
    shape: str = "ellipse"
    rotation_deg: float = 0.0
    corner_radius: float = 0.0
    edge_mode: str | None = None
    strength: str = "standard"
    reason: str = "Manual ellipse cover."


class BlurUpdateIn(BaseModel):
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    shape: str | None = None
    rotation_deg: float | None = None
    corner_radius: float | None = None
    edge_mode: str | None = None
    strength: str | None = None
    reason: str | None = None


class BlurStrengthIn(BaseModel):
    strength: str


class BlurSuggestIn(BaseModel):
    strength: str = "standard"


class GeminiLabelIn(BaseModel):
    image_ids: list[UUID] | None = None
    all_eligible: bool = False
    library_id: UUID | None = None


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
    has_approved_blur: bool = False
    publish_variant: str | None = None
    publish_variant_label: str | None = None
    thumbnail_url: str | None
    preview_url: str | None
    original_preview_url: str | None = None
    publish_preview_url: str | None = None
    blur_unlock_status: str = "none"
    blur_unlock_message: str | None = None
    # Flagged (DO_NOT_POST) or any blur covers: safe/excellent needs a verified blur first.
    blur_verification_required: bool = False
    blur_verified: bool = False
    blur_unlock_highlights: list[dict[str, object]] = Field(default_factory=list)
    gemini_label_status: str | None = None
    gemini_label_at: datetime | None = None
    enrich_hashtags: list[str] = []
    gemini_refused: bool = False
    gemini_refused_reason: str | None = None
    stage: str = "new"
    stage_label: str = "New"
    stage_detail: str | None = None
    stage_caption_ready: bool | None = None
    posted_permalink: str | None = None
    gemini_description: str | None = None
    gemini_mood: str | None = None


class StageCountsOut(BaseModel):
    counts: dict[str, int]
    labels: dict[str, str]


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
    has_tags: bool = False
    has_caption: bool = False
    published: bool = False
    gemini_label_status: str | None = None


class CandidatePage(BaseModel):
    items: list[CandidateOut]
    next_offset: int | None = None


class CandidateBulkIn(BaseModel):
    image_ids: list[UUID] = Field(min_length=1, max_length=500)


class CandidateBulkItemResult(BaseModel):
    image_id: UUID
    ok: bool
    error: str | None = None


class CandidateBulkQueueResult(BaseModel):
    results: list[CandidateBulkItemResult]
    queued_count: int


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


class HealthCheckOut(BaseModel):
    id: str
    label: str
    status: str
    detail: str


class HealthStatusOut(BaseModel):
    overall: str
    checks: list[HealthCheckOut]
    cached_seconds: int = 25


class MusicTrackPatchIn(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, max_length=200)


class ReelMusicIn(BaseModel):
    # null = auto (library rotation/random), "none" = silent, else a music track id.
    choice: str | None = Field(default=None, max_length=64)
