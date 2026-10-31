# baked-superiorsix

Superior Six game API. Identity (sign-in / JWT) lives in **baked-auth**; per-app
profiles in **baked-user**. This service owns the game itself.

## Status

FastAPI + Poetry matching the baked-auth / baked-user layout. `leagues` /
`matches` tables are live. Public game reads:

- `GET /game/livematches?matchDate=dd-mm-yyyy` — Super Six selections (`use_match`)
- `GET /game/matchdetail?hometeam=&awayteam=&matchdate=` — form, H2H, league positions

Fixture/score ingest is **process-driven** via `MatchIngestService` (not user
REST). Admin can list leagues/matches and select slots with `POST /admin/addmatch`.
Other legacy routes remain no-ops for now.

## Requirements

- Python 3.12.3
- [Poetry](https://python-poetry.org/) 2.x

## Setup

```bash
cp .env.example .env
poetry install
```

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
