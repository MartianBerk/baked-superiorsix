# Superior Six — transitional production Docker (`local-prod`)

One `docker compose up` for **baked-auth**, **baked-user**, **baked-superiorsix**, and
**baked-superiorsix-app** on Martin's server. APIs stay on the compose network;
only the front-end nginx container publishes **`:8040`** (configurable via
`FE_HOST_PORT`).

The static web build uses Expo **`experiments.baseUrl` `/super`** and same-origin
API bases (`/auth`, `/user`, `/superiorsix`) baked into the image. Pair this
stack with **host nginx** so the public site serves the app under `/super/` while
API paths sit at the domain root.

For day-to-day Mac/Linux API dev with published backend ports, use
[`local-dev/`](../local-dev/) instead.

## Layout

Clone as **siblings** (names must match build contexts):

```text
parent/
  baked-superiorsix/        ← you are here (local-prod/)
  baked-auth/
  baked-user/
  baked-superiorsix-app/
```

Build contexts: `../../baked-auth`, `../../baked-user`, `..`, and
`../../baked-superiorsix-app`.

Checkout the deploy branch on **both** `baked-superiorsix` and
`baked-superiorsix-app` (e.g. `deploy/local-prod-subpath`).

## Deploy on the server

```bash
cd parent/baked-superiorsix/local-prod
cp .env.example .env
# Edit .env: JWT_SECRET, AUTH_BOOTSTRAP_*, BAKED_AUTH_CLIENT_GIT_TOKEN, CORS as needed

export BAKED_AUTH_CLIENT_GIT_TOKEN=…   # or set in .env

docker compose up --build -d
```

**Bootstrap once** (after health checks pass; no HTTP bootstrap route):

```bash
docker compose exec baked-auth baked-auth-bootstrap
```

Never pass bootstrap passwords on argv. Re-run is safe if the admin user already
exists (membership grant still applies).

### Health checks

Backends (from another container on the same compose network):

```bash
docker compose exec baked-superiorsix-app wget -qO- http://baked-auth:8000/health
docker compose exec baked-superiorsix-app wget -qO- http://baked-user:8000/health
docker compose exec baked-superiorsix-app wget -qO- http://baked-superiorsix:8000/health
```

Front-end proxy + static host:

```bash
curl -s http://127.0.0.1:8040/health
curl -sI http://127.0.0.1:8040/super/ | head -n 1
```

API smoke through the **inner** nginx (same paths host nginx will use):

```bash
curl -s http://127.0.0.1:8040/auth/health
curl -s http://127.0.0.1:8040/user/health
curl -s http://127.0.0.1:8040/superiorsix/health
```

## Env

| Variable | Purpose |
|----------|---------|
| `BAKED_AUTH_CLIENT_GIT_TOKEN` | BuildKit secret for private `baked-auth-client` |
| `JWT_SECRET` | **Required** shared HS256 secret (no dev default) |
| `AUTH_BOOTSTRAP_EMAIL` / `PASSWORD` | **Required** deploy-time auth admin |
| `CORS_ORIGINS` | Quoted JSON — default includes `https://superiorsix.co.uk` |
| `FOOTBALL_DATA_API_TOKEN` / `GOAL_API_KEY` | Optional ingest |
| `FE_HOST_PORT` | Host port for app container (default `8040`) |
| `EXPO_PUBLIC_*` / `EXPO_BASE_URL` | Optional image build overrides (see `.env.example`) |

`AUTH_ENVIRONMENT`, `USER_ENVIRONMENT`, and `SUPERIORSIX_ENVIRONMENT` default to
`production`.

SQLite data: named volumes `baked-*-data` at `/data` in each API container.

## Host nginx (transitional hybrid)

Terminate TLS on the host and proxy to `127.0.0.1:8040`. The **inner** container
nginx already reverse-proxies `/auth/`, `/user/`, and `/superiorsix/` to the
backends on the compose network.

**Strip `/super/`** when forwarding the SPA so the inner server sees paths at `/`
(matching the exported asset layout after prefix strip):

```nginx
# Example server block fragment — adjust ssl_* / server_name for superiorsix.co.uk

location /super/ {
    proxy_pass http://127.0.0.1:8040/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /auth/ {
    proxy_pass http://127.0.0.1:8040/auth/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
}

location /user/ {
    proxy_pass http://127.0.0.1:8040/user/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
}

location /superiorsix/ {
    proxy_pass http://127.0.0.1:8040/superiorsix/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
}
```

Users open `https://superiorsix.co.uk/super/` (trailing slash recommended). Static
bundles reference `/super/…` asset URLs; the host forwards those to the container
after stripping the prefix.

### Future cutover (root-hosted app)

When the marketing site no longer shares the domain:

1. Rebuild **baked-superiorsix-app** with `EXPO_BASE_URL` empty and host-pinned
   `EXPO_PUBLIC_*` URLs (or dedicated API hostnames).
2. Point host nginx `location /` at the app container (no `/super` prefix).
3. Expose APIs on their own hostnames or paths as needed.

## Tear down

```bash
docker compose down          # keep volumes
docker compose down -v       # wipe SQLite volumes
```

## Scheduling

Production ingest is **not** wired inside Compose; schedule jobs on the host
with cron against the running stack. See [`crontab.example`](crontab.example):
set `LOCAL_PROD_DIR` to this directory, create `logs/`, and install the file.

| Concern | What runs | Cadence (Europe/London) |
|--------|-----------|-------------------------|
| **League catalog** | `baked-superiorsix-league-extractor` | Weekly Mon 08:00 — upserts league name/code/season `start_date` only (no `current_matchday` in DB). |
| **Fixtures & scores** | `baked-superiorsix-match-extractor -l {PL,ELC,EL1,EL2} --days-ahead 7` | Mon–Wed 15:00, Thu 12:00, Fri 15:00 (5m stagger per league); Sat 21:00 and Sun 18:00 for post-match sync. Omitting `--from` uses the incomplete-fixture anchor from [PR #54](https://github.com/martianberk/baked-superiorsix/pull/54) — rebuild the API image with that CLI before deploy. |
| **Live scoring** | `baked-superiorsix-auto-score` | Sat only: every 15m from 14:30 through 19:00 (single pass per invocation). |

## Related docs

- App image / subpath build: `baked-superiorsix-app` README — *Production subpath (/super)*.
- Local dev stack with published API ports: [`../local-dev/README.md`](../local-dev/README.md).
