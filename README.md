# Instamagic

Local review for a photography archive. It scans a folder you already have, classifies and crops on this machine, asks Gemini for captions after you approve a frame, and can publish to Instagram through the **official Graph API** when you click Publish. Originals stay read-only.

This is a single-operator app. It is not a follower, like, or comment spam tool. Comment drafts are copy-paste assists only.

## What you can do

1. Point it at one library folder.
2. Scan JPG, JPEG, PNG, and WEBP. RAW files (CR2, CR3, NEF, ARW, DNG) are recorded and skipped.
3. Review a dark contact sheet (cover tiles). Keys: `1` reject, `2` review, `3` safe, `4` excellent, arrows, space classify (rate-and-advance), `C` crops, `B` blur, `P` queue, `G` captions. ⌘/Ctrl-click multi-select; Shift-click range.
4. Approve a non-destructive crop when the local model says `SAFE_AFTER_CROP`.
5. Optionally open **Curate**: Gemini ranks SAFE/EXCELLENT frames, suggests tags + captions as drafts. You reorder, accept/reject, edit, then **Send to publish queue** only for what you approve.
6. Queue a frame (or receive it from Curate), generate/edit captions if needed, approve, then **Publish** to Instagram (human-gated).
7. Keep a **Related accounts** research list and draft insightful comments for you to post manually.

Queueing, captions, curation, and publish all require an adult attestation on the library and a human rating of safe or excellent. A model state of `DO_NOT_POST` cannot be overridden into the queue or publish path. Curation never auto-queues or auto-publishes.

## Before a real library

Read `backend/instamagic/services/safety/decision.py` and `backend/instamagic/services/gemini/context.py`. The cropper exists to keep unsuitable regions out of a post. It is not a way around moderation. The app never estimates age. You attest, in the exact sentence the setup form shows, that every person in that library is a consenting adult.

The default safety classifier is ONNX and fails closed. If the weight file is missing, every image stays in `REVIEW` and nothing is sent out. Weights are not in git. Run `scripts/download-models.sh` yourself only if you accept the weight license the script names. A checksum in `SAFETY_MODEL_SHA256` is required before a download is kept.

`SAFETY_CLASSIFIER=fake` is a filename stand-in for the fixture library. Do not point it at real photographs.

## Run (dev)

```sh
cp .env.example .env
# Set APP_PASSWORD (12+ characters), APP_SECRET (16+), and POSTGRES_PASSWORD.
# Leave GEMINI_API_KEY / Instagram tokens empty until you want live captions or publish.
docker compose up -d --build
```

Open http://127.0.0.1:5173. The API is on 127.0.0.1:8000 and is not published on any other interface. Inside Compose the library mount is `/data/originals` (read-only). Set `ORIGINALS_ROOT` in `.env` to an existing host folder before `compose up`. Compose enables `REQUIRE_EXTERNAL_LIBRARY_MOUNTS` so API and workers refuse to start or scan if that bind is missing (Docker's empty stub directory). Containers are named `instamagic-*` for easy `docker ps` identification.

## Phase 2: Gemini + Instagram

### Gemini captions

1. Set `GEMINI_API_KEY` in `.env` (Google AI Studio / Gemini API).
2. Keep `GEMINI_IMAGE_POLICY=text_only` unless you intentionally allow approved preview pixels (`safe_previews`).
3. Optional: `ACCOUNT_VOICE` to steer caption and comment-draft tone.
4. From Review (`G`) or Queue → Generate captions. Or use **Curate** for a batch of ranked picks with tag + caption drafts. Missing key returns a clear error; scan/review/queue keep working.

### Instagram Graph publish (human-gated)

1. Convert the Instagram account to **Professional** (Business or Creator) and link it to a **Facebook Page**.
2. Create a Meta app with Instagram Graph permissions (typically `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` — confirm current Meta docs).
3. Generate a long-lived Page access token that can act on the Instagram Business Account.
4. Set in `.env` (never commit):
   - `INSTAGRAM_PROVIDER=graph`
   - `INSTAGRAM_ACCESS_TOKEN=…`
   - `INSTAGRAM_BUSINESS_ACCOUNT_ID=…` (IG user id, not the Page id)
5. **Public image URL:** Meta’s servers must `GET` the JPEG. Set `PUBLIC_BASE_URL` to an **https** origin reachable from the public internet that fronts this API (or a tunnel). LAN-only stage (`instamagic.schimmel.homes` behind `lan-only`) is **not** enough for Meta. Publish mints a short-lived HMAC URL at `/api/public/publish-assets/{token}`.
6. Restart the API so env reloads. Open **Instagram** in the app nav (`/instagram`) → **Test connection / Refresh account details** to verify the token and confirm username, counts, account type, and linked Page id (token is masked; never pasted into the UI).
7. Flow: Queue → Approve → Generate/edit caption → **Publish to Instagram**. Gates re-run at publish time. The Instagram helper only inspects — it does not publish.

`AUTO_PUBLISH` defaults to `false`. Enabling it requires the exact ack `AUTO_PUBLISH_ACK=I_UNDERSTAND_AUTO_PUBLISH_RISKS`. Phase 2 still expects a human Publish click; auto-fire is reserved and not the default path. Compose hardcodes `AUTO_PUBLISH=false` on services.

Credentials stay in host `.env` (or the secret store that injects env). There is no in-app token paste that writes `.env` inside the container. For CI / local gate tests: `INSTAGRAM_PROVIDER=fake` and `GEMINI_PROVIDER=fake` (never against real photos in stage/production).

## Stage (LAN)

On `10.1.1.8` at `/opt/docker/instamagic`, behind Traefik as **https://instamagic.schimmel.homes** (LAN-only). Stage **pulls** images built by GitHub Actions (no `build:` on the host). From this repo: `scripts/deploy-stage.sh` (copies compose + env example only). Details and required `DOCKERHUB_*` settings in `docs/operations.md`.

Put `GEMINI_API_KEY` and Instagram tokens only in the host `.env` (chmod 600). Do not copy local Mac secrets to stage. For real Instagram publish from stage, also set a public `PUBLIC_BASE_URL` Meta can reach.

Stop the stack when you are done:

```sh
docker compose down
```

## Fixture demo

The fixture library is not a real detector demo. It only exercises the gates.

```sh
scripts/make-fixture-library.py
SAFETY_CLASSIFIER=fake GEMINI_PROVIDER=fake INSTAGRAM_PROVIDER=fake PIPELINE_MODE=inline docker compose up -d --build
```

Add library path `/data/originals`, check the attestation box, and scan. `unsafe-portrait.jpg` can become `SAFE_AFTER_CROP`. `blocked-frame.jpg` stays `DO_NOT_POST`. `clean.jpg` stays `REVIEW` until you rate it. The banner on screen means the fake classifier is on.

## Layout

Python package `instamagic` lives under `backend/instamagic` (API, models, services, workers). The React app is `frontend/`. Compose, Dockerfiles, and scripts are at the repo root. See `DEVELOPMENT.md` for the service map and what is intentionally not built yet.

## Threat model (Phase 2 surfaces)

| Surface | Risk | Mitigation |
|---|---|---|
| `GEMINI_API_KEY` | Key theft → billed abuse / prompt leakage | Env only; never log; audit rows store context hash + text context, not originals by default |
| Gemini payloads | Path / pixel leak from library | `services/gemini/context.py` + `assert_safe_gemini_payload`; default `text_only` |
| `INSTAGRAM_ACCESS_TOKEN` | Account takeover / unwanted posts | Env/secret store only; never logged; human Publish + gates |
| Publish asset URLs | Unauthenticated JPEG fetch | Short-lived HMAC token; relative derivative path only; no session cookie |
| `AUTO_PUBLISH` | Unattended posts | Fail-closed unless exact ack phrase; Compose forces false |
| Related accounts / drafts | Temptation to automate engagement | CRUD + drafts only; no follow/like/comment Graph calls |
