# Development

## Layout

The plan sketch `backend/api` is the Python package `instamagic`, installed from `backend/`:

| Area | Path |
|---|---|
| HTTP | `backend/instamagic/api`, `main.py` |
| Models | `backend/instamagic/models` |
| Services | `images`, `safety`, `scoring`, `crops`, `blurs`, `gemini`, `instagram`, `recommendations` |
| Workers | `backend/instamagic/workers` (`io` and `ml` queues) |
| Migrations | `backend/alembic` |
| UI | `frontend/src` |

Uvicorn loads `instamagic.main:create_app` with `--factory`. Importing the package does not require a live database.

## Decisions that are already locked

- No age model. Adulthood is an operator attestation stored on the library, with the exact statement in `config.ATTESTATION_STATEMENT`.
- `model_state` and `human_rating` are separate. Aesthetic score never changes the safety state. A human rating changes the badge, and it still cannot queue or caption a `DO_NOT_POST` frame or an unattested library.
- No exposed region means `REVIEW`, not automatic `SAFE`. `SAFE_AFTER_CROP` only when a candidate crop keeps the face and excludes exposed boxes. If every crop still contains one, the state is `DO_NOT_POST`.
- Missing or uncertain detector fails closed to `REVIEW`. The app does not import NudeNet. The ONNX decoder is local. Weights stay outside git at `SAFETY_MODEL_PATH`.
- Originals are mounted read-only. The browser and Gemini receive EXIF-stripped derivatives only. GPS is `has_gps`, never coordinates. Default `GEMINI_IMAGE_POLICY` is `text_only`.
- RQ with queues `io` and `ml`, not Celery. `PIPELINE_MODE=inline` runs the scan in the API process for tests and the fixture demo. `queue` enqueues it.
- One password, HMAC session cookie, bind `127.0.0.1` unless `ALLOW_CONTAINER_BIND=true` inside Compose. Published ports stay on `127.0.0.1`.
- Instagram publish uses the official Graph Content Publishing API only (`services/instagram/client.py`). Human-gated by default (`AUTO_PUBLISH=false`). Fake provider for CI.
- Related accounts and comment drafts are research / assisted copy-paste. No follow, like, or comment automation.
- Calendar drag-and-drop, Reels, Stories, comment inbox sync, experiments, and dashboards are still later.

This is one local operator, not a multi-replica platform service. There is no SSO, no second replica, and no shared secret store beyond `.env` on this machine.

## Blur covers

Blur covers sit alongside crops for `SAFE_AFTER_CROP` frames. Suggested ellipses come from clear exposed `SafetyRegion` boxes (expanded, feathered). The operator can also add a manual ellipse and choose strength (`subtle` / `standard` / `strong`); sigma stays high enough that detail inside the cover is not recognizable.

Covers are **derivatives only**. The library root stays read-only; originals are never rewritten. Rendering builds a feathered elliptical mask, Gaussian-blurs the RGB image, and composites only under the mask. Derivatives are served through the same `safe_join` path jail as crop previews.

An approved blur set can satisfy the queue and Gemini gates the same way an approved crop does, but only when the opaque (feathered) core of the covers fully contains every clear exposed region. A miss fails closed. `DO_NOT_POST` stays blocked even with an approved blur. Missing attestation still blocks queue and Gemini. Aesthetic scores never change safety state.

## EXIF time

Capture timestamps are stored timezone-aware and labeled UTC because EXIF has no offset. Treat them as camera-local clock values, not a verified UTC instant.

## Tests

```sh
cd backend && .venv/bin/pytest -q
cd backend && .venv/bin/ruff check instamagic tests
cd frontend && npx tsc --noEmit
```

Tests use SQLite and the fake classifier. They do not download weights and they do not call Gemini. The Gemini firewall test fails if a context payload contains a filesystem path.

## Still later

CSV or JSON metric import so ranking can use real performance without waiting on insights scopes. Then: comment inbox sync (read-only), experiments, calendar scheduling (Meta schedule is limited for organic IG feed), Reels/Stories, embedding clusters, and dashboard charts. `ImageEmbedding` and `InstagramMetrics` remain mostly unused by product UI.

## Review before real photos

`services/safety/decision.py` is the state table. `services/gemini/context.py` is the only builder allowed to assemble a Gemini request. `services/images/paths.py` is the derivative jail. Nothing in this repo is deployed on a public interface.
