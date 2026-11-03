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

Fixture/score ingest is **process-driven** via `MatchIngestService` and CLI
extractors (not user REST). Admin can open rounds, list leagues/matches, and
select slots with `POST /admin/addmatch`. Other legacy routes remain no-ops for
now.

### Ingest extractors (SS-7 / SS-10)

| CLI | Purpose |
|-----|---------|
| `poetry run baked-superiorsix-league-extractor` | Upsert league catalog (name / code / start date) |
| `poetry run baked-superiorsix-match-extractor -l PL --from 2026-11-01 [--to 2026-11-03]` | Upsert fixtures for **one** league over an inclusive date range |
| `poetry run baked-superiorsix-match-extractor -l EL1 --live` | Upsert in-play fixtures (Goal API live feed; football-data ≈ today) |
| `poetry run baked-superiorsix-db leagues` | List leagues in the DB (TSV) |
| `poetry run baked-superiorsix-db matches [-l PL] [-d 14-03-2026] …` | List matches with optional filters (TSV) |

Providers:

| Concern | Provider |
|---------|----------|
| League catalog (`league-extractor`) | football-data.org **v2** (England area; includes EL1/EL2) |
| Match fixtures `EL1` / `EL2` | [Goal API](https://goal-api.com/documentation) (`/fixtures/date/:date`, paginated) |
| Match fixtures everything else (e.g. `PL`, `ELC`) | football-data.org v4 date range |
| `--live` for EL1/EL2 | Goal API `/fixtures/live` |
| `--live` for PL/ELC | football-data.org `status=LIVE` (`IN_PLAY` + `PAUSED`) |

Goal API has no date-range filter on the date endpoint, so a `--from`/`--to` window
issues one request per calendar day (with `limit`/`offset` pagination).

Env vars (also accept `SUPERIORSIX_` prefix):

- `FOOTBALL_DATA_API_TOKEN` — football-data.org `X-Auth-Token` (optional for league list; required for PL/ELC match extract)
- `GOAL_API_KEY` — Goal API bearer token (EL1/EL2 match extract)

No Flashscore / Selenium. Match extract may still apply scores when the fixture
payload already includes them (`--live` is the near-real-time path).

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
