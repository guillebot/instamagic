# Instamagic

Local review for a photography archive. It scans a folder you already have, classifies and crops on this machine, asks Gemini for captions after you approve a frame, and can publish to Instagram through the **official Graph API** when you click Publish. Originals stay read-only.

This is a single-operator app. It is not a follower, like, or comment spam tool. Comment drafts are copy-paste assists only.

## What you can do

1. Point it at one library folder.
2. Scan JPG, JPEG, PNG, and WEBP. RAW files (CR2, CR3, NEF, ARW, DNG) are recorded and skipped.
3. **Review** — scan, rate (`1` reject … `4` excellent), crop (`C`) / blur (`B`) when the model says safe-after-crop. Badges: Safe (originally clear) vs Publishable (remediated + rated). See `docs/lifecycle.md`.
4. **Curate** — Gemini ranks Safe/Publishable frames, suggests tags + captions. Reorder, accept/reject, then send to the publish queue.
5. **Queue** — human last word: approve, edit captions, **Publish** to Instagram (human-gated), queue **carousels** (2–10 gated frames, shared caption), or **Schedule** explicit slots / auto-fill from the calendar UI.
6. Keep a **Related accounts** research list and draft insightful comments for you to post manually.

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
2. Default `GEMINI_IMAGE_POLICY=preview` lets Curate/captions attach EXIF-stripped preview/thumb JPEGs for Safe/Publishable frames only (toggle on Curate). Use `text_only` to opt out of pixels; `manual_only` blocks batch Gemini.
3. Optional: `ACCOUNT_VOICE` to steer caption and comment-draft tone.
4. From Review (`G`) or Queue → Generate captions. Or use **Curate** for a batch of ranked picks with tag + caption drafts. Missing key returns a clear error; scan/review/queue keep working.

### Instagram Graph publish (human-gated)

1. Convert the Instagram account to **Professional** (Business or Creator) and link it to a **Facebook Page**.
2. Create a Meta app ([developers.facebook.com/apps](https://developers.facebook.com/apps/)):
   - App type **Business** → add **Facebook Login for Business** (Page-linked IG).
   - **App Domains:** `schimmel.homes`
   - **Valid OAuth Redirect URI:** `https://instamagic.schimmel.homes/api/instagram/oauth/callback` (defaults from `FRONTEND_ORIGIN`, not `PUBLIC_BASE_URL`).
   - Permissions (when `META_OAUTH_CONFIG_ID` is unset): `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management` — or set `META_OAUTH_CONFIG_ID` from the Login configuration in the App Dashboard.
   - **Development** mode: app admins/testers only. **Live** + App Review for other users.
   - Set `INSTAGRAM_GRAPH_VERSION=v25.0` (or newer supported version; default in repo).
   - If you manage multiple Pages, set `INSTAGRAM_EXPECTED_USERNAME=foto.sch` (no `@`) so OAuth picks the right IG account.
3. Open **Instagram** (`/instagram`) → paste **App ID** + **App Secret** from Settings → Basic → Save (sealed in Postgres; no `META_*` in `.env` required). Optional host env remains a fallback. Do **not** paste secrets into chat.
4. Click **Connect with Instagram / Meta**. Flow: Meta login → CSRF `state` → code exchange → long-lived user token → Page token + IG business account id → sealed in Postgres → account profile cached. Advanced collapse still allows paste; host `INSTAGRAM_ACCESS_TOKEN` / `INSTAGRAM_BUSINESS_ACCOUNT_ID` override DB when set.
5. **Test connection / Refresh account details** shows a clear error if not configured. Token stays masked; never logged.
6. **Public image URL (publish only):** Meta’s servers must `GET` the JPEG. On stage/production, set `PUBLIC_BASE_URL` to a public **https** origin (e.g. Cloudflare Tunnel) that forwards **only** `/api/public/publish-assets/*` to Instamagic. LAN-only Traefik (`FRONTEND_ORIGIN`) works for **OAuth login** but not for Meta’s publish crawler. The `/instagram` page warns when `PUBLIC_BASE_URL` is missing or equals the LAN UI host.
7. Flow: Queue → Approve → Generate/edit caption → **Publish to Instagram**. Gates re-run at publish time. The Instagram helper only inspects — it does not post.

`AUTO_PUBLISH` defaults to `false`. Enabling it requires the exact ack `AUTO_PUBLISH_ACK=I_UNDERSTAND_AUTO_PUBLISH_RISKS`. Phase 2 still expects a human Publish click; auto-fire is reserved and not the default path. Compose hardcodes `AUTO_PUBLISH=false` on services.

Never commit tokens. For CI / local gate tests: `INSTAGRAM_PROVIDER=fake` and `GEMINI_PROVIDER=fake` (never against real photos in stage/production).

## Stage (LAN)

On `10.1.1.8` at `/opt/docker/instamagic`, behind Traefik as **https://instamagic.schimmel.homes** (LAN-only). Stage **pulls** images built by GitHub Actions (no `build:` on the host). From this repo: `scripts/deploy-stage.sh` (copies compose + env example only). Details and required `DOCKERHUB_*` settings in `docs/operations.md`.

Put `GEMINI_API_KEY` only in the host `.env` (chmod 600). Prefer Meta App ID/Secret + **Connect with Instagram / Meta** on `/instagram` so secrets stay sealed in Postgres (optional `META_*` env fallback). Do not copy local Mac secrets to stage. For real Instagram publish from stage, also set a public `PUBLIC_BASE_URL` Meta can reach (OAuth itself works on LAN Traefik).

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
| Gemini payloads | Path / pixel leak from library | `assert_safe_gemini_payload`; derivatives only under `preview` + Curate toggle; never originals / file URIs |
| `INSTAGRAM_ACCESS_TOKEN` | Account takeover / unwanted posts | Env/secret store only; never logged; human Publish + gates |
| Publish asset URLs | Unauthenticated JPEG fetch | Short-lived HMAC token; relative derivative path only; no session cookie |
| `AUTO_PUBLISH` | Unattended posts | Fail-closed unless exact ack phrase; Compose forces false |
| Related accounts / drafts | Temptation to automate engagement | CRUD + drafts only; no follow/like/comment Graph calls |
