# baked-superiorsix

Superior Six game API. Identity (sign-in / JWT) lives in **baked-auth**; per-app
profiles in **baked-user**. This service owns the game itself.

## Status

Scaffold: FastAPI + Poetry. Legacy supersix routes are mounted as **no-ops**
(path prefixes replace the old subdomains). Role requirements are declared for
baked-auth roles but not enforced yet.

## Setup

```bash
poetry install
poetry run uvicorn baked_superiorsix.main:app --reload --port 8002
```

- Health: `GET /health`
- Docs: http://127.0.0.1:8002/docs

## Tests

```bash
poetry run pytest
```

## Route map

Legacy subdomain → path prefix: `game`, `user`, `admin`, `stats`, `euros`,
`worldcup`, plus aliases `eurowizard` and `qatarhero` where the old app used
alternate subdomains for the same handlers.
