# baked-superiorsix

Superior Six game API. Identity (sign-in / JWT) lives in **baked-auth**; per-app
profiles in **baked-user**. This service owns the game itself.

## Status

Scaffold: FastAPI + Poetry matching the baked-auth / baked-user layout. Legacy
supersix routes are mounted as **no-ops** (path prefixes replace the old
subdomains). Role-gated routes verify baked-auth JWTs and require the declared
roles.

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
