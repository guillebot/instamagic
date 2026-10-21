from __future__ import annotations

CLEAR_SCORE = 0.45
AMBIGUOUS_SCORE = 0.25

# Class order expected from a YOLOv8 ONNX detector with the public 18-label contract.
# Instamagic does not import the AGPL NudeNet package. Weights, if used, are operator-supplied.
MODEL_LABELS: tuple[str, ...] = (
    "FEMALE_GENITALIA_COVERED",
    "FACE_FEMALE",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "ANUS_EXPOSED",
    "FEET_EXPOSED",
    "BELLY_COVERED",
    "FEET_COVERED",
    "ARMPITS_COVERED",
    "ARMPITS_EXPOSED",
    "FACE_MALE",
    "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_COVERED",
    "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
)

EXPOSED_LABELS = frozenset(
    {
        "BUTTOCKS_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "ANUS_EXPOSED",
    }
)

EXPLICIT_LABELS = frozenset(
    {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "ANUS_EXPOSED",
    }
)

NUDITY_LABELS = frozenset(
    {
        "BUTTOCKS_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "ANUS_EXPOSED",
    }
)

LINGERIE_LABELS = frozenset(
    {
        "FEMALE_GENITALIA_COVERED",
        "FEMALE_BREAST_COVERED",
        "BUTTOCKS_COVERED",
        "ANUS_COVERED",
    }
)

SKIN_LABELS = EXPOSED_LABELS | frozenset({"BELLY_EXPOSED", "FEET_EXPOSED", "ARMPITS_EXPOSED"})
