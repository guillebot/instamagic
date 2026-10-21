# Operations

## Bind

Compose publishes frontend `127.0.0.1:5173`, API `127.0.0.1:8000`, Postgres `127.0.0.1:5432`, and Redis `127.0.0.1:6379`. Container processes set `ALLOW_CONTAINER_BIND=true` so they can listen on `0.0.0.0` inside the bridge network. Do not publish those ports on `0.0.0.0`.

`AUTO_PUBLISH=false` is set again on the services. Startup exits if the value is true.

## Data

| Data | Where | Notes |
|---|---|---|
| Originals | Host path in `ORIGINALS_ROOT`, mounted at `/data/originals:ro` | Never deleted by the app or by a database restore |
| Derivatives | Docker volume `derivatives` | Safe to delete; scan again to rebuild |
| Postgres | Volume `postgres_data` | `scripts/backup-db.sh` writes `data/backups/` |
| Weights | `SAFETY_MODEL_PATH`, gitignored | Not downloaded during image build or startup |
| Secrets | `.env`, gitignored | Password, app secret, Postgres password, optional Gemini key |

Logs are JSON. The logger redacts fields whose names contain password, token, secret, authorization, or api_key. Do not log image bodies.

## Workers

`worker-io` and `worker-ml` run `python -m instamagic.workers io|ml`. With `PIPELINE_MODE=inline` the API scans in process and the workers stay idle. With `queue`, scan jobs land on `io`.

## Tear down

```sh
docker compose down
```

That stops the containers. Volumes remain until you remove them yourself. Removing `postgres_data` drops the catalog of paths, ratings, and captions. It does not remove the photo library.
