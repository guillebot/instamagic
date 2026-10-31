# Operations

## Environments

| Env | Where | Compose | Images | URL |
|---|---|---|---|---|
| **dev** | Local Mac | `docker-compose.yml` only | `build:` locally | http://127.0.0.1:5173 |
| **stage** | `10.1.1.8` `/opt/docker/instamagic` | `docker-compose.stage.yml` (deployed as `docker-compose.yml`) | pull from registry | https://instamagic.schimmel.homes |

Containers are named `instamagic-*` (`instamagic-backend`, `instamagic-frontend`, …) via `container_name` and compose project `name: instamagic`.

## Images (CI)

Workflow: `.github/workflows/docker-publish.yml` (on `main` / `master`, tags `v*`, and `workflow_dispatch`).

| Image | Serves |
|---|---|
| `<prefix>/instamagic-backend` | API, migrate, worker-io, worker-ml |
| `<prefix>/instamagic-frontend` | nginx SPA |

Tags: **`YYYYMMDD-XX`** (preferred pin), `latest` (default branch), `sha-<shortsha>`, semver / `v*` tag names, branch name.

### Build version (`YYYYMMDD-XX`)

- **Format:** calendar date + sequential build that day (`01`, `02`, …).
- **Timezone:** `America/Argentina/Buenos_Aires` (local release day). Documented here so CI and operators stay consistent; do not mix UTC dates for the same release.
- **Injection:** CI sets `APP_VERSION` as a Docker build-arg. Backend image gets `ENV APP_VERSION`; frontend bake-in is `VITE_APP_VERSION`. Running API exposes it on `/health` (public) and `/api/config` (authed). UI shows the same id in the topbar chip and on the login screen.
- **Computation:** `scripts/compute-app-version.sh` (used by `docker-publish.yml`) lists existing GHCR tags for `instamagic-backend` matching today’s date and increments `XX`.
- **Stage pin:** after publish, set host `.env` `IMAGE_TAG` to that version (e.g. `IMAGE_PREFIX=ghcr.io/guillebot IMAGE_TAG=20261022-01 scripts/deploy-stage.sh`).

**Registry preference:** Docker Hub when GitHub Actions variable `DOCKERHUB_USERNAME` and secret `DOCKERHUB_TOKEN` are set (same names as Metrik/MonkeyTracker; org already has `DOCKERHUB_USERNAME=gschimmel`). GHCR (`ghcr.io/guillebot/instamagic-*`) is always pushed as a fallback. Stage `.env` `IMAGE_PREFIX` must match the Hub namespace you pull from (default `gschimmel`), or `ghcr.io/guillebot` for GHCR-only.

Required GitHub settings (do not invent tokens):

| Name | Type | Purpose |
|---|---|---|
| `DOCKERHUB_USERNAME` | Actions **variable** | Hub namespace — already `gschimmel` at org for Metrik |
| `DOCKERHUB_TOKEN` | Actions **secret** | Hub access token with write (reuse org secret if present) |

Platform: `linux/amd64` only (stage host is amd64; Apple Silicon local builds must not be what stage pulls).

## Bind (dev)

Compose publishes frontend `127.0.0.1:5173`, API `127.0.0.1:8000`, Postgres `127.0.0.1:5432`, and Redis `127.0.0.1:6379`. Container processes set `ALLOW_CONTAINER_BIND=true` so they can listen on `0.0.0.0` inside the bridge network. Do not publish those ports on `0.0.0.0`.

`AUTO_PUBLISH=false` is hardcoded on Compose services. Startup refuses `AUTO_PUBLISH=true` unless `AUTO_PUBLISH_ACK=I_UNDERSTAND_AUTO_PUBLISH_RISKS`. Prefer human Publish in the Queue UI.

## Stage (LAN host)

Classic pattern on `10.1.1.8`: stack under `/opt/docker/instamagic`. Only **frontend** joins external Docker network `guille-net` (Traefik); postgres/redis/backend/workers stay on project network `instamagic_internal`. Putting DB on `guille-net` advertises the bare DNS alias `postgres` to every other stack and breaks siblings (e.g. monkeytracker). Traefik terminates TLS with Cloudflare DNS-01 (`letsencrypt-dns`), middleware `lan-only@file` (allowlist `10.1.1.0/24`, `10.1.2.0/24`, Docker bridge).

Host layout (slim — no app source, no Dockerfiles):

```
/opt/docker/instamagic/
  docker-compose.yml      # copy of repo docker-compose.stage.yml
  stage.env.example
  .env                    # secrets + IMAGE_PREFIX / IMAGE_TAG (chmod 600)
  models/320n.onnx        # ONNX weights (not in image)
  # Photo library is host /fotosarte → ORIGINALS_ROOT (not under this tree)
```

Deploy / update from the Mac (does not copy local `.env` or source):

```sh
scripts/deploy-stage.sh
```

That copies compose + env example, ensures `.env` exists, then `docker compose pull && up -d`.

Or on the host after images exist:

```sh
cd /opt/docker/instamagic
# pin a release: edit IMAGE_TAG=20261022-01 (or sha-…)
docker compose pull
docker compose up -d --force-recreate
```

From the Mac after CI finishes:

```sh
IMAGE_PREFIX=ghcr.io/guillebot IMAGE_TAG=20261022-01 scripts/deploy-stage.sh
curl -fsS https://instamagic.schimmel.homes/health   # {"status":"ok","version":"20261022-01"}
```

Stage defaults (`deploy/stage.env.example`): `ORIGINALS_ROOT=/fotosarte`, `APP_UID=1000` / `APP_GID=100` (host `guille` / `users`), `SAFETY_CLASSIFIER=onnx` + required `SAFETY_MODEL_SHA256`, `AUTO_PUBLISH=false`, `FRONTEND_ORIGIN=https://instamagic.schimmel.homes`. `SAFETY_CLASSIFIER=fake` is refused when `ENVIRONMENT=stage`.

**Library permissions:** `/fotosarte` is typically `770 guille:users`. Nested folders may be `711`. Do **not** chmod/chown the archive for Docker. Compose runs backend/workers as `APP_UID:APP_GID` so the process is the library owner (and skips the image entrypoint's drop to uid `10001`). `migrate` stays on the image default. After changing `APP_UID`, chown only app-writable data (named volume `derivatives`, never `/fotosarte`):

```sh
docker run --rm -v instamagic_derivatives:/data/derivatives alpine \
  chown -R 1000:100 /data/derivatives
```

Health from LAN:

```sh
curl -fsS https://instamagic.schimmel.homes/health
```

App login password is `APP_PASSWORD` in the host `.env` (generated on first deploy; not in git).

Frontend nginx proxies `/api`, `/health`, and `/ready` to the Compose service name `backend` using Docker's embedded DNS (`127.0.0.11`) and a variable `proxy_pass`, so the upstream IP is re-resolved after the backend container is recreated. If you still see `502` / `Connection refused` to an old bridge IP, recreate frontend: `docker compose up -d --force-recreate frontend`.

## Data

| Data | Where | Notes |
|---|---|---|
| Originals | Host path in `ORIGINALS_ROOT` (stage: `/fotosarte`), mounted at `/data/originals:ro` | Never deleted by the app or by a database restore. Compose sets `REQUIRE_EXTERNAL_LIBRARY_MOUNTS=true` so API and workers fail closed if that path is not a real host bind (Docker's empty stub directory). Recreate containers after fixing `ORIGINALS_ROOT`. Keep host mode `770`; match `APP_UID`/`APP_GID` instead of opening permissions. |
| Derivatives | Docker volume `derivatives` | Writable by `APP_UID`. Safe to delete; scan again to rebuild |
| Postgres | Volume `postgres_data` | `scripts/backup-db.sh` writes `data/backups/` |
| Weights | `./models` on host → `/models` | Not baked into the image; checksum via `SAFETY_MODEL_SHA256` |
| Secrets | `.env`, gitignored | Password, app secret, Postgres password, optional Gemini key, Instagram Graph token |

## Phase 2 env (stage host `.env`)

| Variable | Notes |
|---|---|
| `GEMINI_API_KEY` | Live captions / drafts. Empty → clear 503 on generate. |
| `GEMINI_IMAGE_POLICY` | Default `preview` (Curate toggle may send derivative JPEGs). `text_only` opts out; `manual_only` blocks batch Gemini. |
| `META_APP_ID` / `META_APP_SECRET` | Optional host fallback. Prefer pasting App ID + Secret on `/instagram` (sealed in Postgres; DB wins over env). Enables **Connect with Instagram / Meta**. Callback: `{FRONTEND_ORIGIN}/api/instagram/oauth/callback` (stage: `https://instamagic.schimmel.homes/api/instagram/oauth/callback`). Add that exact URI under Facebook Login → Valid OAuth Redirect URIs. Never log secrets. |
| `META_OAUTH_REDIRECT_URI` | Optional override of the callback URL. Defaults from `FRONTEND_ORIGIN`, not `PUBLIC_BASE_URL`. |
| `INSTAGRAM_ACCESS_TOKEN` / `INSTAGRAM_BUSINESS_ACCOUNT_ID` | Optional env override (wins over sealed DB). Prefer OAuth or in-app paste. Never log. |
| `PUBLIC_BASE_URL` | Public https Meta can fetch for **publish** JPEGs. LAN-only Traefik is enough for OAuth (browser redirect) but insufficient for publish crawl. |
| `AUTO_PUBLISH` | Keep `false` on stage. |

### Meta Developer Console checklist (OAuth)

1. Create app at [developers.facebook.com/apps](https://developers.facebook.com/apps/) → use case **Other** → type **Business** → add **Facebook Login**.
2. Valid OAuth Redirect URI: `https://instamagic.schimmel.homes/api/instagram/oauth/callback` (same host as Traefik; `/api` is already proxied; Strict Mode exact match).
3. Permissions used by Instamagic: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`.
4. IG account must be Professional and linked to a Page you admin.
5. On `/instagram`, paste App ID + App Secret → Save (sealed with `APP_SECRET`). Host `META_*` env is optional fallback. Then **Connect with Instagram / Meta**.
6. CSRF: authorize URL carries a signed `state` (10 min TTL). Callback exchanges code server-side; tokens are sealed and never returned or logged in full.

Logs are JSON. The logger redacts fields whose names contain password, token, secret, authorization, or api_key. Do not log image bodies.

## System health bar

After login, the top header shows a compact health strip (refreshed about every 30 seconds). It calls `GET /api/health/status` (session cookie required — not exposed under `/api/public`).

| Indicator | Meaning |
|---|---|
| Library | Registered library path readable; derivatives dir writable; free space on the library volume |
| Instagram | Sealed credentials present; cached `@username` vs `INSTAGRAM_EXPECTED_USERNAME` when set |
| Publish URL | `PUBLIC_BASE_URL` is HTTPS and distinct from the LAN UI origin; optional cached HEAD probe on `/api/public/publish-assets/` (409 = route OK) |
| Database | Postgres reachable; Alembic at head |
| Queue | Redis reachable; `worker-io` / `worker-ml` via RQ or Redis heartbeats (`PIPELINE_MODE=queue`) |
| Gemini / Data disk | API key configured (never shown); free space under `DERIVATIVES_ROOT` |

Responses are cached server-side for ~25s and omit secrets, tokens, and env values.

## Workers

`worker-io` and `worker-ml` run `python -m instamagic.workers io|ml` from the same backend image. Each worker writes a short-lived Redis heartbeat key (`instamagic:worker:heartbeat:<queue>`) so the health bar can detect stalled workers when RQ introspection is empty. With `PIPELINE_MODE=inline` the API scans in process and the workers stay idle. With `queue`, scan jobs land on `io`.

## Tear down

```sh
docker compose down
```

That stops the containers. Volumes remain until you remove them yourself. Removing `postgres_data` drops the catalog of paths, ratings, and captions. It does not remove the photo library.

## Growth features (Reel schedule, music, collaborators, curated hashtags, content mix, carousel builder)

- **Reel schedule** (Schedule → Reel schedule). Reels have their own lane: default slots 15:00 and 21:00
  (editable list), "Reels per day" (default 2) and a 1 h gap between Reels. Reels never count toward the feed's
  posts/day or min gap, and feed photo slots (e.g. 11:00 / 19:00) are never converted into Reels. The old
  "Reel version" behaviour from 0028 is gone (migration 0029 re-enables auto-slot for any original it had left
  unscheduled). Reels publish as `media_type=REELS` with `share_to_feed` from this setting (default on) and a
  `cover_url` (the verified feed derivative).
- **Reel proposals**. Every hour the io worker (and Queue → "Propose Reels") tops up Reel candidates until
  there are `2 × Reels per day` waiting or approved: 3–6 publishable photos from the same shoot (carousel
  builder in Reel mode: never a photo that is already in a Reel, at most a third skin/body shots, cover never a
  skin/body shot), rendered from the verified blurred feed derivatives. They show in the Queue as
  "Reel · needs approval"; nothing is approved, scheduled or published until you approve each one. A candidate
  needs a caption before it can be scheduled (pre-filled from the cover photo when it already has one).
- **Music library** (top nav → Music). Upload royalty-free / properly licensed tracks only (mp3, m4a, aac, wav,
  ogg, flac; max 40 MB; 3 s–20 min; LAN only). Files live on the host in `${MUSIC_HOST_DIR:-./data/music}`
  (`/opt/docker/instamagic/data/music` on stage), mounted at `/data/music` in backend and worker-io
  (`MUSIC_ROOT`). Each Reel gets a track baked into the MP4 (looped/trimmed to the video, fade in/out, loudness
  normalised): auto = rotation (least recently used) or random (Schedule → Reel schedule → music pick), or a
  specific track / silent per Reel in the Queue. Changing a Reel's music marks the render stale. With an empty
  library Reels render silent and show a warning badge. The Graph API can't attach Instagram's licensed audio.
- **Collaborators** (Queue card → Collaborators). Up to 3 validated handles, sent as the Graph `collaborators`
  parameter on feed images, carousel parent containers and Reels, never on Stories or carousel children.
  They're saved per shoot (session, otherwise folder) and pre-filled for that shoot's other photos. The invited
  account has to accept before the post shows on its profile.
- **Hashtags**. Each post gets 5–8 shuffled tags from the niche pool (B&W photos also draw from the mono pool),
  plus 2–3 Gemini tags that pass the allowlist (pools + built-in content allowlist + Settings "extra"). Generic
  tags (monochrome, blackandwhite, photography, art, fineartphotography) are dropped, and the blocklist still
  applies at generation and at publish. Pools are edited in Schedule → Hashtag pools.
- **Content mix**. See the docstring in `backend/instamagic/services/posting/content_mix.py` for the heuristic.
  It uses local signals only. Colour photos, portraits with a face, and still lifes rank first in Fill slots and
  Curate/Next best. Skin/body/tattoo/projected-light shots are capped at 1 in any 5 consecutive feed posts on the
  auto-scheduling paths. They're also avoided as carousel covers.
- **Carousel builder** (Queue → Carousel builder). Suggests 3–5 photos from the same shoot. Each one is
  SAFE/EXCELLENT, or a DO_NOT_POST photo with a verified blur (the blurred render is what gets published), and
  the cover isn't a skin/body shot. You can reorder, remove, create a carousel (approve/schedule as one post)
  or make a Reel slideshow.
