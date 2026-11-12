# Superior Six — local Mac / Linux backend stack

One compose file that builds and runs **baked-auth**, **baked-user**, and
**baked-superiorsix** together (SQLite volumes, shared JWT secret, published
ports). This is the entry point for testing the whole backend.

Front end lives in **baked-superiorsix-app** (host Expo primary; optional
container pointing at these published ports).

## Layout

Clone the repos as **siblings** of `baked-superiorsix` (names must match):

```text
parent/
  baked-superiorsix/     ← you are here (local-dev/)
  baked-auth/
  baked-user/
  baked-superiorsix-app/ ← Expo / optional nginx on :8040
```

Build contexts: `../../baked-auth`, `../../baked-user`, and `..` (this repo).

Root `docker-compose.yml` still builds **only** the superiorsix image for
standalone rebuilds; use this `local-dev/` stack for the full backend.

## Ports

| Service | Host | Container | Docs |
|---------|------|-----------|------|
| baked-auth | `http://127.0.0.1:8010` | `:8000` | `/docs` |
| baked-user | `http://127.0.0.1:8020` | `:8000` | `/docs` |
| baked-superiorsix | `http://127.0.0.1:8030` | `:8000` | `/docs` |

Inter-service URLs inside the compose network use Docker DNS
(`http://baked-auth:8000`, `http://baked-user:8000`), not host ports.

## Quick start

```bash
cd baked-superiorsix/local-dev
cp .env.example .env
# set BAKED_AUTH_CLIENT_GIT_TOKEN (PAT, Contents: Read on baked-auth-client)
export BAKED_AUTH_CLIENT_GIT_TOKEN=…   # or leave it in .env for Compose
# Keep CORS_ORIGINS / JWT_PREVIOUS_SECRET_KEYS single-quoted JSON strings in .env

docker compose up --build
```

Health checks:

```bash
curl -s http://127.0.0.1:8010/health
curl -s http://127.0.0.1:8020/health
curl -s http://127.0.0.1:8030/health
```

## Env (shared)

| Variable | Purpose |
|----------|---------|
| `BAKED_AUTH_CLIENT_GIT_TOKEN` | BuildKit secret for private `baked-auth-client` git dep |
| `JWT_SECRET` | Shared HS256 secret → `AUTH_` / `USER_` / `SUPERIORSIX_JWT_SECRET_KEY` |
| `USER_JWT_AUDIENCE` / `SUPERIORSIX_JWT_AUDIENCE` | Must be `superiorsix` (JWT `aud`) |
| `AUTH_BOOTSTRAP_EMAIL` / `AUTH_BOOTSTRAP_PASSWORD` | Deploy-time auth admin (CLI); also wired into superiorsix for inviteplayer → `POST /v1/users` (`aud=auth`) |
| `SUPERIORSIX_AUTH_ADMIN_EMAIL` / `PASSWORD` | Optional override for invite auth-admin (defaults nest to `AUTH_BOOTSTRAP_*`) |
| `CORS_ORIGINS` | Expo web + optional `:8040` origins — **quote as JSON**: `CORS_ORIGINS='["http://localhost:8081",…]'` (unquoted `[…]` is mangled by Compose and crashes auth) |
| `FOOTBALL_DATA_API_TOKEN` / `GOAL_API_KEY` | Optional match ingest |

SQLite files live in named volumes `baked-*-data` under `/data` in each container.

## Bootstrap the auth admin (+ superiorsix ops membership)

There is **no** HTTP bootstrap route. After the stack is up:

```bash
docker compose exec baked-auth baked-auth-bootstrap
```

Credentials come from `AUTH_BOOTSTRAP_*` in `.env` (default `ops` /
`LocalDevChangeMe1`). **Never** pass the password on argv.

The CLI:

1. Creates the auth-audience `admin` user (skips if one already exists).
2. Grants that user an active `superiorsix` AppMembership with
   `admin` and `user-profile` (creates/merges if missing;
   no-ops when already present).

Safe to re-run on an existing volume where `ops` already exists — the
superiorsix membership grant still applies. After bootstrap, ops can log in
with `audience=superiorsix` as well as `audience=auth`.

```bash
curl -s -X POST http://127.0.0.1:8010/v1/auth/token \
  -d 'email=ops@example.com&password=LocalDevChangeMe1&audience=superiorsix'
```

## Invite flow (Superior Six)

Superior Six roster is **invite-only** (baked-user). baked-auth provides
invite-ready users + set-password claim (SS-66 / baked-auth#14). Roles and
membership are applied **at invite**; acceptinvite only creates the roster.

1. **Bootstrap** (above) — ops already has superiorsix `admin` /
   `user-profile`. The same bootstrap credentials are passed to superiorsix as
   `SUPERIORSIX_AUTH_ADMIN_*` so **inviteplayer** can call baked-auth
   `POST /v1/users` with `aud=auth` (passwordless create + membership).
2. **Invite** (superiorsix `admin` token, `audience=superiorsix`):
   `POST http://127.0.0.1:8030/admin/inviteplayer`
   `{"email":"martin@example.com","first_name":"…","last_name":"…","roles":["player"]}`
   Selectable roles: `player`, `worldcup`, `euros`.
   Superiorsix `admin` is never assignable from the app (bootstrap / direct auth).
   `user-profile` is always merged server-side (not selectable).
   Roster role edit: `GET /admin/getplayerroles?email=` /
   `POST /admin/updateroles` (preserves bootstrap `admin`).
3. **Set password** (invitee claim):
   `POST http://127.0.0.1:8010/v1/auth/set-password`
   `{"email":"martin@example.com","password":"…"}`.
   `/token` returns **403** until the password is set.
4. **Accept** (invitee):
   `POST http://127.0.0.1:8030/user/acceptinvite`
   `{"email":"martin@example.com","password":"…"}`
   BFF: login invitee (`audience=superiorsix`) → create roster profile (consumes
   invite). No service bootstrap password on accept. Returns player +
   `access_token` + roles.

## Seed local players + fixtures

After the stack is up (and `FOOTBALL_DATA_API_TOKEN` is set in `.env` for
football-data.org), run:

```bash
cd baked-superiorsix/local-dev
chmod +x seed-dev-data.sh   # once
./seed-dev-data.sh
```

The script:

1. Waits for auth + superiorsix health on host ports `8010` / `8030`.
2. Runs `docker compose exec baked-auth baked-auth-bootstrap`.
3. Invites / claims / accepts **three players** (`alice@`, `bob@`, `cara@example.com`)
   with role `player` (password `LocalDevPlayer1`). All three stay players only;
   match-day / roster admin uses bootstrap ops (`admin`). Skips emails already
   on the roster.
4. Runs the in-container extractors (same CLIs as the root README):
   - `baked-superiorsix-league-extractor`
   - `baked-superiorsix-match-extractor -l PL --from 2026-10-31 --to 2026-11-07`
   - `baked-superiorsix-match-extractor -l ELC --from 2026-10-31 --to 2026-11-07`

Overrides (optional): `SEED_FROM`, `SEED_TO`, `SEED_LEAGUES`, `SEED_PLAYER_PASSWORD`,
`AUTH_URL`, `SUPERSIX_URL`. Extractors upsert, so re-runs are safe.

Wipe and start clean with `docker compose down -v` if you want a fresh DB
(no Alembic — recreate volumes is the local reset).

## Point the Expo app at this stack

Primary path: APIs here, Expo on the **host** in `baked-superiorsix-app`:

```bash
cd ../baked-superiorsix-app   # sibling checkout
cp .env.example .env
# EXPO_PUBLIC_AUTH_URL=http://127.0.0.1:8010
# EXPO_PUBLIC_USER_URL=http://127.0.0.1:8020
# EXPO_PUBLIC_SUPERSIX_URL=http://127.0.0.1:8030
npx expo start --web
```

Optional nginx web UI on `:8040` is owned by that app repo’s compose
(`docker compose up --build` there), with upstreams defaulting to
`host.docker.internal:8010/8020/8030`.

## Tear down

```bash
docker compose down          # keep volumes
docker compose down -v       # wipe SQLite volumes
```
