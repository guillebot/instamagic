from __future__ import annotations

from dataclasses import dataclass

from instamagic.services.geometry import Box

ASPECTS: dict[str, tuple[int, int]] = {
    "4:5": (4, 5),
    "1:1": (1, 1),
    "9:16": (9, 16),
}


@dataclass(frozen=True)
class CropWindow:
    x: int
    y: int
    width: int
    height: int
    aspect_ratio: str
    reason: str
    contains_face: bool
    excludes_unsafe: bool

    def as_box(self) -> Box:
        return Box(self.x, self.y, self.width, self.height)


def generate_crops(
    image_width: int,
    image_height: int,
    faces: list[Box],
    unsafe_boxes: list[Box],
) -> list[CropWindow]:
    primary = max(faces, key=lambda box: box.area) if faces else None
    crops = [
        CropWindow(
            x=0,
            y=0,
            width=image_width,
            height=image_height,
            aspect_ratio="original",
            reason="Full frame. Stored so the original composition stays available.",
            contains_face=primary is not None,
            excludes_unsafe=not _hits_any(Box(0, 0, image_width, image_height), unsafe_boxes),
        )
    ]
    for name, (aspect_w, aspect_h) in ASPECTS.items():
        found = _search(image_width, image_height, aspect_w / aspect_h, name, primary, unsafe_boxes)
        if found is not None:
            crops.append(found)
    return crops


def viable_safety_crop(crops: list[CropWindow]) -> bool:
    return any(
        crop.aspect_ratio != "original" and crop.contains_face and crop.excludes_unsafe
        for crop in crops
    )


def window_excludes_unsafe(window: Box, unsafe_boxes: list[Box], margin: int = 8) -> bool:
    return not _hits_any(window, unsafe_boxes, margin)


def aspect_ratio_value(aspect_ratio: str, width: int, height: int) -> float:
    """Locked ratio for a named crop, or width/height for freeform labels like original."""
    if aspect_ratio in ASPECTS:
        aspect_w, aspect_h = ASPECTS[aspect_ratio]
        return aspect_w / aspect_h
    if height <= 0:
        return 1.0
    return width / height


def validate_crop_window(
    *,
    image_width: int,
    image_height: int,
    x: int,
    y: int,
    width: int,
    height: int,
    aspect_ratio: str,
    reference_width: int,
    reference_height: int,
) -> Box:
    if width < 1 or height < 1:
        raise ValueError("Crop size must be at least 1×1.")
    if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
        raise ValueError("Crop must stay inside the image bounds.")
    expected = aspect_ratio_value(aspect_ratio, reference_width, reference_height)
    actual = width / height
    if abs(actual - expected) > 0.02:
        raise ValueError("Crop aspect ratio must match the selected suggestion.")
    return Box(x, y, width, height)


def _hits_any(window: Box, unsafe_boxes: list[Box], margin: int = 8) -> bool:
    expanded = [box.expanded(margin) for box in unsafe_boxes]
    return any(window.intersects(box) for box in expanded)


def _search(
    image_width: int,
    image_height: int,
    target: float,
    aspect_name: str,
    face: Box | None,
    unsafe_boxes: list[Box],
) -> CropWindow | None:
    if image_width < 2 or image_height < 2:
        return None
    if image_width / image_height > target:
        max_h = image_height
        max_w = max(1, int(round(max_h * target)))
    else:
        max_w = image_width
        max_h = max(1, int(round(max_w / target)))
    best: tuple[float, CropWindow] | None = None
    pad = int(0.08 * face.width) if face else 0
    face_needed = face.expanded(pad) if face else None
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
        width = max(1, int(max_w * scale))
        height = max(1, int(max_h * scale))
        if width > image_width or height > image_height:
            continue
        if face_needed and (width < face_needed.width or height < face_needed.height):
            continue
        step_x = max(1, width // 12)
        step_y = max(1, height // 12)
        for y in range(0, image_height - height + 1, step_y):
            for x in range(0, image_width - width + 1, step_x):
                window = Box(x, y, width, height)
                contains_face = face_needed is None or window.contains(face_needed)
                if face is not None and not contains_face:
                    continue
                excludes = not _hits_any(window, unsafe_boxes)
                if unsafe_boxes and not excludes:
                    continue
                score = _score(window, face, scale)
                reason = _reason(aspect_name, face is not None, excludes, bool(unsafe_boxes))
                crop = CropWindow(
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    aspect_ratio=aspect_name,
                    reason=reason,
                    contains_face=face is not None and contains_face,
                    excludes_unsafe=excludes,
                )
                if best is None or score > best[0]:
                    best = (score, crop)
    return best[1] if best else None


def _score(window: Box, face: Box | None, scale: float) -> float:
    size_bonus = scale
    if face is None:
        return size_bonus
    fx = (face.cx - window.x) / window.width
    fy = (face.cy - window.y) / window.height
    thirds = -abs(fy - 0.33) - min(abs(fx - 0.33), abs(fx - 0.66))
    return size_bonus + thirds


def _reason(aspect_name: str, has_face: bool, excludes: bool, had_unsafe: bool) -> str:
    parts = [f"{aspect_name} crop"]
    if has_face:
        parts.append("keeps the face in the upper third when the frame allows it")
    if had_unsafe and excludes:
        parts.append("excludes detected unsafe regions")
    elif not had_unsafe:
        parts.append("composition suggestion only; no unsafe region was detected")
    return ". ".join(parts) + "."
