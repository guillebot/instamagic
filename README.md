# Instamagic

Local review for a photography archive. It scans a folder you already have, classifies and crops on this machine, and asks Gemini for captions only after you approve a frame. Originals stay read-only. Nothing is published to Instagram in this version.

This is a single-operator app bound to localhost. It is not a follower, like, or comment tool.

## What you can do

1. Point it at one library folder.
2. Scan JPG, JPEG, PNG, and WEBP. RAW files (CR2, CR3, NEF, ARW, DNG) are recorded and skipped.
3. Review a dark contact sheet. Keys: `1` reject, `2` review, `3` safe, `4` excellent, arrows, space preview, `C` crops, `P` queue, `G` captions.
4. Approve a non-destructive crop when the local model says `SAFE_AFTER_CROP`.
5. Queue a frame, then ask for caption options.

Queueing and captions both require an adult attestation on the library and a human rating of safe or excellent. A model state of `DO_NOT_POST` cannot be overridden into the queue.

## Before a real library

Read `backend/instamagic/services/safety/decision.py` and `backend/instamagic/services/gemini/context.py`. The cropper exists to keep unsuitable regions out of a post. It is not a way around moderation. The app never estimates age. You attest, in the exact sentence the setup form shows, that every person in that library is a consenting adult.

The default safety classifier is ONNX and fails closed. If the weight file is missing, every image stays in `REVIEW` and nothing is sent out. Weights are not in git. Run `scripts/download-models.sh` yourself only if you accept the weight license the script names. A checksum in `SAFETY_MODEL_SHA256` is required before a download is kept.

`SAFETY_CLASSIFIER=fake` is a filename stand-in for the fixture library. Do not point it at real photographs.

## Run

```sh
cp .env.example .env
# Set APP_PASSWORD (12+ characters), APP_SECRET (16+), and POSTGRES_PASSWORD.
# Leave GEMINI_API_KEY empty until you want live captions.
docker compose up -d --build
```

Open http://127.0.0.1:5173. The API is on 127.0.0.1:8000 and is not published on any other interface. Inside Compose the library mount is `/data/originals` (read-only). Set `ORIGINALS_ROOT` in `.env` to the host folder you want mounted there.

Missing `GEMINI_API_KEY` returns a clear caption error. Scan, review, crops, and the queue keep working.

`AUTO_PUBLISH` must stay false. The process refuses to start if it is true. There is no Instagram publisher in this milestone; the client raises not-configured even if tokens are present.

Stop the stack when you are done:

```sh
docker compose down
```

## Fixture demo

The fixture library is not a real detector demo. It only exercises the gates.

```sh
scripts/make-fixture-library.py
SAFETY_CLASSIFIER=fake GEMINI_PROVIDER=fake PIPELINE_MODE=inline docker compose up -d --build
```

Add library path `/data/originals`, check the attestation box, and scan. `unsafe-portrait.jpg` can become `SAFE_AFTER_CROP`. `blocked-frame.jpg` stays `DO_NOT_POST`. `clean.jpg` stays `REVIEW` until you rate it. The banner on screen means the fake classifier is on.

## Layout

Python package `instamagic` lives under `backend/instamagic` (API, models, services, workers). The React app is `frontend/`. Compose, Dockerfiles, and scripts are at the repo root. See `DEVELOPMENT.md` for the service map and what is intentionally not built yet.
