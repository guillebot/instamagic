# Architecture

Picture lifecycle (Review → Curate → Queue) and Safe vs Publishable badges: [`lifecycle.md`](lifecycle.md).

Three zones:

1. Originals. A read-only mount. The scanner records a relative path, hashes, and `has_gps`. It does not write into the library and it does not put coordinates or original bytes on an API response.
2. Local derivatives and scores. Thumbnails (long edge 256) and previews (long edge 1600) are new JPEGs under `DERIVATIVES_ROOT`, orientation applied, EXIF removed. Safety regions, crops, ratings, and the queue live in Postgres. Grid thumb/preview GETs serve `{image_id}/thumb.jpg` and `preview.jpg` without a DB round-trip.
3. Gemini. Only after the library is attested, a human has marked the frame safe or excellent, and the model state is not `DO_NOT_POST` or an unconfirmed `REVIEW`. `SAFE_AFTER_CROP` text describes the approved crop. Derivative preview/thumb pixels may go out when the env ceiling is `preview` / `safe_previews` / `selected` and Curate sets `include_images` (or captions use the same ceiling) — never originals or path URIs. Opt out with `text_only`. The audit row stores a hash of the text context (and whether pixels were attached), not the photograph file.

Per-image labeling (`POST /api/gemini/label`, worker-io) runs one Gemini call per eligible frame that lacks `gemini_label_status=ok` or a terminal `blocked:*`. Results land in `image_tags` (`source=gemini`), `captions`, and `images.gemini_description` / `gemini_mood`. Blocked responses are stored as `blocked:<reason>` and are not retried; transient errors use `error` with attempt backoff. Batch curation (`POST /api/curation`) is a separate worker job: it ranks from stored labels (text-only chunks of 32) and only attaches preview pixels for frames already labeled `ok`. A blocked frame in a chunk is bisected out so the rest of the batch still completes.

## Safety

`SafetyClassifier` returns boxes, not one NSFW score. Exposed labels can force `DO_NOT_POST` or `SAFE_AFTER_CROP`. Covered and ambiguous regions do not auto-clear a frame. OpenCV YuNet supplies the face box the crop must keep. The fake classifier replaces both the detector and the face finder, and it is refused when `ENVIRONMENT=production`.

Crops are rows (`x`, `y`, `width`, `height`, `aspect_ratio`, `reason`) for original, 4:5, 1:1, and 9:16. Approving one renders a derivative. The original file is not opened for write.

## Ranking

Next best posts uses human rating, Laplacian sharpness, thirds and face placement, and dHash uniqueness. Near-duplicates collapse to the stronger frame. With no imported Instagram metrics the reason text says the rank is local only.

## Phase 2 publishing

Queue states: `CANDIDATE` → `APPROVED` → (`PUBLISHING`) → `PUBLISHED` or `FAILED`. Publish re-runs `publishing_gate` (attestation, human safe/excellent, not `DO_NOT_POST`, crop/blur when required), mints a short-lived public derivative URL, then Graph `/{ig-user-id}/media` → `media_publish`. Tokens stay in env. `INSTAGRAM_PROVIDER=fake` records calls for CI.

Growth helpers: `related_accounts` CRUD, Gemini comment drafts and hashtag suggestions. Drafts are never posted by the API.

## Interfaces left empty

`AestheticScorer`, `CompositionAnalyzer`, and the embedding table can be replaced later (OpenCLIP is the intended swap) without changing the queue gates. Metrics import and Insights dashboards are not wired yet.
