"""DB-backed blur coverage gate thresholds (singleton posting_schedule_configs row)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

DEFAULT_COVERAGE_THRESHOLD_PERCENT = 95
MIN_COVERAGE_THRESHOLD_PERCENT = 60
MAX_COVERAGE_THRESHOLD_PERCENT = 100

DEFAULT_REGION_PADDING_PX = 6
MIN_REGION_PADDING_PX = 0
MAX_REGION_PADDING_PX = 20


@dataclass(frozen=True)
class BlurCoverageSettings:
    threshold_percent: int
    padding_px: int

    @property
    def min_ratio(self) -> float:
        return self.threshold_percent / 100.0


def _clamp_threshold(value: int) -> int:
    return max(MIN_COVERAGE_THRESHOLD_PERCENT, min(MAX_COVERAGE_THRESHOLD_PERCENT, int(value)))


def _clamp_padding(value: int) -> int:
    return max(MIN_REGION_PADDING_PX, min(MAX_REGION_PADDING_PX, int(value)))


def validate_threshold_percent(value: int) -> int:
    if value < MIN_COVERAGE_THRESHOLD_PERCENT or value > MAX_COVERAGE_THRESHOLD_PERCENT:
        raise ValueError(
            f"blur_coverage_threshold must be between {MIN_COVERAGE_THRESHOLD_PERCENT} and "
            f"{MAX_COVERAGE_THRESHOLD_PERCENT} (percent)."
        )
    return value


def validate_padding_px(value: int) -> int:
    if value < MIN_REGION_PADDING_PX or value > MAX_REGION_PADDING_PX:
        raise ValueError(
            f"blur_region_padding_px must be between {MIN_REGION_PADDING_PX} and {MAX_REGION_PADDING_PX}."
        )
    return value


def _from_row(row: object) -> BlurCoverageSettings:
    threshold = getattr(row, "blur_coverage_threshold", None)
    padding = getattr(row, "blur_region_padding_px", None)
    return BlurCoverageSettings(
        threshold_percent=_clamp_threshold(
            threshold if threshold is not None else DEFAULT_COVERAGE_THRESHOLD_PERCENT
        ),
        padding_px=_clamp_padding(padding if padding is not None else DEFAULT_REGION_PADDING_PX),
    )


def blur_coverage_settings(session: Session) -> BlurCoverageSettings:
    from instamagic.services.posting.schedule_config import get_or_create_config

    return _from_row(get_or_create_config(session))
