from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from instamagic.models.enums import (
    CaptionStatus,
    CommentDraftStatus,
    HumanRating,
    JobKind,
    JobStatus,
    QueueState,
    SafetyState,
    TagSource,
)


class EmbeddingType(TypeDecorator):
    """pgvector on PostgreSQL, bytes on other dialects so unit tests can use SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(512))
        return dialect.type_descriptor(Text())


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    root_path: Mapped[str] = mapped_column(String(1024), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attestations: Mapped[list[AdultAttestation]] = relationship(back_populates="library")
    images: Mapped[list[Image]] = relationship(back_populates="library")


class AdultAttestation(Base):
    __tablename__ = "adult_attestations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    statement: Mapped[str] = mapped_column(Text)
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    library: Mapped[Library] = relationship(back_populates="attestations")


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (UniqueConstraint("library_id", "relative_path", name="uq_image_library_path"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(512))
    file_size: Mapped[int] = mapped_column(Integer)
    file_mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    camera: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    lens: Mapped[str | None] = mapped_column(String(200), nullable=True)
    focal_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    iso: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shutter: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aperture: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_gps: Mapped[bool] = mapped_column(Boolean, default=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    session_name: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_state: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    human_rating: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)

    library: Mapped[Library] = relationship(back_populates="images")
    ratings: Mapped[list[ImageRating]] = relationship(back_populates="image")
    assessments: Mapped[list[SafetyAssessment]] = relationship(back_populates="image")
    crops: Mapped[list[CropSuggestion]] = relationship(back_populates="image")
    blurs: Mapped[list[BlurCover]] = relationship(back_populates="image")
    captions: Mapped[list[Caption]] = relationship(back_populates="image")
    queue_items: Mapped[list[PublishingQueueItem]] = relationship(back_populates="image")
    score: Mapped[ImageScore | None] = relationship(back_populates="image")
    tags: Mapped[list[ImageTag]] = relationship(back_populates="image")


class ImageDuplicate(Base):
    __tablename__ = "image_duplicates"
    __table_args__ = (UniqueConstraint("library_id", "relative_path", name="uq_duplicate_path"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    library_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageRating(Base):
    __tablename__ = "image_ratings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    rating: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="ratings")


class SafetyAssessment(Base):
    __tablename__ = "safety_assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    nudity_score: Mapped[float] = mapped_column(Float, default=0)
    explicit_score: Mapped[float] = mapped_column(Float, default=0)
    lingerie_score: Mapped[float] = mapped_column(Float, default=0)
    skin_exposure_score: Mapped[float] = mapped_column(Float, default=0)
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    instagram_safe_score: Mapped[float] = mapped_column(Float, default=0)
    crop_possible: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    detector_available: Mapped[bool] = mapped_column(Boolean, default=False)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    model_state: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(64), default="none")
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="assessments")
    regions: Mapped[list[SafetyRegion]] = relationship(back_populates="assessment")


class SafetyRegion(Base):
    __tablename__ = "safety_regions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("safety_assessments.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    unsafe: Mapped[bool] = mapped_column(Boolean, default=False)

    assessment: Mapped[SafetyAssessment] = relationship(back_populates="regions")


class CropSuggestion(Base):
    __tablename__ = "crop_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    contains_face: Mapped[bool] = mapped_column(Boolean, default=False)
    excludes_unsafe: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    derivative_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="crops")


class BlurCover(Base):
    """Non-destructive elliptical cover stored as a derivative only — originals stay read-only."""

    __tablename__ = "blur_covers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    shape: Mapped[str] = mapped_column(String(16), default="ellipse")
    feather: Mapped[int] = mapped_column(Integer, default=8)
    radius: Mapped[float] = mapped_column(Float, default=20.0)
    strength: Mapped[str] = mapped_column(String(16), default="standard")
    reason: Mapped[str] = mapped_column(Text)
    covers_unsafe: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    derivative_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="blurs")


class ImageScore(Base):
    __tablename__ = "image_scores"

    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    sharpness: Mapped[float] = mapped_column(Float, default=0)
    composition: Mapped[float] = mapped_column(Float, default=0)
    face_quality: Mapped[float] = mapped_column(Float, default=0)
    technical_quality: Mapped[float] = mapped_column(Float, default=0)
    aesthetic_score: Mapped[float] = mapped_column(Float, default=0)
    uniqueness: Mapped[float] = mapped_column(Float, default=0)

    image: Mapped[Image] = relationship(back_populates="score")


class ContentTag(Base):
    __tablename__ = "content_tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageTag(Base):
    __tablename__ = "image_tags"
    __table_args__ = (UniqueConstraint("image_id", "tag_id", name="uq_image_tag"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_tags.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16), default=TagSource.HUMAN.value)

    image: Mapped[Image] = relationship(back_populates="tags")
    tag: Mapped[ContentTag] = relationship()


class Caption(Base):
    __tablename__ = "captions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    style: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=CaptionStatus.GENERATED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())

    image: Mapped[Image] = relationship(back_populates="captions")


class PublishingQueueItem(Base):
    __tablename__ = "publishing_queue_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    crop_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crop_suggestions.id", ondelete="SET NULL"), nullable=True
    )
    caption_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("captions.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), default=QueueState.CANDIDATE.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    image: Mapped[Image] = relationship(back_populates="queue_items")


class GeminiRequest(Base):
    __tablename__ = "gemini_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), index=True)
    policy: Mapped[str] = mapped_column(String(32))
    context_sha256: Mapped[str] = mapped_column(String(64))
    context_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), default=JobKind.SCAN.value, index=True)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value, index=True)
    library_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    files_processed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    files_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    files_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    files_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageEmbedding(Base):
    __tablename__ = "image_embeddings"

    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), default="pending")
    embedding: Mapped[str | None] = mapped_column(EmbeddingType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstagramPost(Base):
    __tablename__ = "instagram_posts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("images.id", ondelete="SET NULL"), nullable=True, index=True
    )
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publishing_queue_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ig_media_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    ig_container_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="published")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permalink: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RelatedAccount(Base):
    """Research list of accounts the operator studies — not follow/like/comment automation."""

    __tablename__ = "related_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    handle: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_relevant: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), onupdate=_now
    )


class CommentDraft(Base):
    """Operator-assisted reply drafts. Never posted automatically to Instagram."""

    __tablename__ = "comment_drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("images.id", ondelete="SET NULL"), nullable=True, index=True
    )
    related_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("related_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_context: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=CommentDraftStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())


class InstagramMetrics(Base):
    __tablename__ = "instagram_metrics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instagram_posts.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_visits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    follows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    save_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    share_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    profile_visit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    follow_conversion: Mapped[float | None] = mapped_column(Float, nullable=True)
    reach_to_followers_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)


# Imported by decision code via the string values stored on Image.
SAFETY_VALUES = {state.value for state in SafetyState}
RATING_VALUES = {rating.value for rating in HumanRating}
