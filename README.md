# baked-superiorsix

Superior Six game API. Identity (sign-in / JWT) lives in **baked-auth**; per-app
profiles in **baked-user**. This service owns the game itself.

## Status

FastAPI + Poetry matching the baked-auth / baked-user layout. `leagues` /
`matches` tables are live. Public game reads:

- `GET /game/livematches?matchDate=dd-mm-yyyy` — Super Six selections (`use_match`)
- `GET /game/matchdetail?hometeam=&awayteam=&matchdate=` — form, H2H, league positions
- `POST /user/addprediction` / `GET /user/getprediction?gameId=` — player submissions
  (JWT `PLAYER`, keyed by auth id; needs an open round + selected match; 10m cutoff)

Fixture/score ingest is **process-driven** via `MatchIngestService` (not user
REST). Admin can open rounds, list leagues/matches, and select slots with
`POST /admin/addmatch`. Other legacy routes remain no-ops for now.

## Requirements

- Python 3.12.3
- [Poetry](https://python-poetry.org/) 2.x

## Setup

```bash
cp .env.example .env
poetry install
```

`poetry install` (and `docker compose build`) need read access to the private
[`baked-auth-client`](https://github.com/MartianBerk/baked-auth-client) git
dependency. For local compose builds, export `BAKED_AUTH_CLIENT_GIT_TOKEN` (a
GitHub PAT with repo read access) or put it in `.env`. The deploy-dev workflow
reads the same name from GitHub Actions secrets.

Set `SUPERIORSIX_JWT_SECRET_KEY` to the same value as `AUTH_JWT_SECRET_KEY` (and
keep previous secrets in `SUPERIORSIX_JWT_PREVIOUS_SECRET_KEYS` during rotation).

## Run

```bash
poetry run baked-superiorsix
```

OpenAPI docs are at [http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs).

- Health: `GET /health`

## Tests

```bash
poetry run pytest
```

## Route map

Legacy subdomain → path prefix: `game`, `user`, `admin`, `stats`, `euros`,
`worldcup`, plus aliases `eurowizard` and `qatarhero` where the old app used
alternate subdomains for the same handlers.
