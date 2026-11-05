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
| `AUTH_BOOTSTRAP_USERNAME` / `AUTH_BOOTSTRAP_PASSWORD` | Deploy-time auth admin (CLI only) |
| `CORS_ORIGINS` | Expo web + optional `:8040` origins — **quote as JSON**: `CORS_ORIGINS='["http://localhost:8081",…]'` (unquoted `[…]` is mangled by Compose and crashes auth) |
| `FOOTBALL_DATA_API_TOKEN` / `GOAL_API_KEY` | Optional match ingest |

SQLite files live in named volumes `baked-*-data` under `/data` in each container.

## Bootstrap the auth admin (+ superiorsix ops membership)

There is **no** HTTP bootstrap route. After the stack is up:

```bash
docker compose exec baked-auth baked-auth-bootstrap
```

Credentials come from `AUTH_BOOTSTRAP_*` in `.env` (default `ops` /
`local-dev-change-me`). **Never** pass the password on argv.

The CLI:

1. Creates the auth-audience `admin` user (skips if one already exists).
2. Grants that user an active `superiorsix` AppMembership with
   `admin`, `game-master`, and `user-profile` (creates/merges if missing;
   no-ops when already present).

Safe to re-run on an existing volume where `ops` already exists — the
superiorsix membership grant still applies. After bootstrap, ops can log in
with `audience=superiorsix` as well as `audience=auth`.

```bash
curl -s -X POST http://127.0.0.1:8010/v1/auth/token \
  -d 'username=ops&password=local-dev-change-me&audience=superiorsix'
```

## Invite flow (Superior Six)

Superior Six roster is **invite-only** (baked-user). baked-auth is identity only.

1. **Bootstrap** (above) — ops already has superiorsix `admin` / `game-master` /
   `user-profile`.
2. **Register** the player:
   `POST http://127.0.0.1:8010/v1/auth/register` `{"username":"martin","password":"…"}`.
3. **Grant Superior Six membership** for the player (auth admin token,
   `audience=auth`):
   `PUT http://127.0.0.1:8010/v1/applications/superiorsix/memberships/martin`
   `{"roles":["player","user-profile"]}` — add `admin` / `game-master` only if
   that person should run ops too.
4. **Invite** the username onto the roster (superiorsix `admin` token,
   `audience=superiorsix`):
   - via game API: `POST http://127.0.0.1:8030/admin/inviteplayer`
     `{"username":"martin","first_name":"…","last_name":"…"}`
   - or baked-user: `POST http://127.0.0.1:8020/superiorsix/invites` …
5. Player logs in with `audience=superiorsix`, then accepts the invite by creating
   a profile (`POST /superiorsix/users` on baked-user, or
   `POST /admin/addplayer` as admin).

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
