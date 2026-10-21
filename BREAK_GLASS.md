# Break glass

Instamagic has no remote admin and no cloud console. Recovery is on this machine.

## Lock yourself out of the app

The only login is `APP_PASSWORD` in `.env`. To rotate it, change that value and recreate the backend and workers:

```sh
docker compose up -d --force-recreate backend worker-io worker-ml
```

Existing session cookies stop working because they are signed with `APP_SECRET`. Rotating `APP_SECRET` logs every session out. Neither value is stored in the database.

## Restore the database

Originals are not in Postgres and a restore does not delete them. Derivatives can be rebuilt with another scan.

```sh
scripts/backup-db.sh
scripts/restore-db.sh data/backups/instamagic-TIMESTAMP.sql
```

Both scripts talk to the Compose Postgres on this host. They do not open a network path to the library.

## If the process is bound off localhost

Startup refuses a non-loopback bind unless `ALLOW_CONTAINER_BIND=true`. That flag is for the container process only. Compose still publishes `127.0.0.1`. Do not add a public port mapping to get in. If a port was changed by hand, set it back and recreate the stack.
