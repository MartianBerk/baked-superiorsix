# baked-superiorsix

Superior Six game API. Identity (sign-in / JWT) lives in **baked-auth**; per-app
profiles / roster in **baked-user**. This service owns the game itself and does
**not** invent a second permissions model — roles come from JWT `aud` + `roles`.

## Status

FastAPI + Poetry matching the baked-auth / baked-user layout. `leagues` /
`matches` tables are live. Public game reads:

- `GET /game/meta` — header ``round_number`` (latest slate sequence) +
  ``prize_pot_pence`` (`buy_in × full active roster × sequence`), plus subrounds /
  match-day list for navigation, and ``teams`` (legacy ``TEAM_XREF`` map:
  provider ``team_name`` → display / stats ``xref``). FE labels use
  ``meta.teams[match.home_team] || match.home_team``. Form, H2H, and league
  tables aggregate via xref aliases so renames / provider migrations stay
  continuous. Admin: `GET /admin/listteamxref`, `POST /admin/upsertteamxref`.
- `GET /game/currentround` — public pot summary (`jackpot` / `prize_pot_pence`);
  empty when no open parent round
- `GET /game/livescores` — aggregate scoreboard for latest slate (or
  `?subroundId=` / legacy `?matchDate=dd-mm-yyyy`); includes per-player picks,
  ``correct`` flags, top-level ``matches`` with live scores, ``live`` /
  ``next_kickoff`` for FE polling
- `GET /game/livematches?subroundId=` — Super Six selections for one slate
  (preferred); legacy `?matchDate=dd-mm-yyyy` for a UTC day. Includes frozen
  prediction ``difficulty`` when set on the match row
- `GET /game/matchdetail?hometeam=&awayteam=&matchdate=` — form, H2H, league positions,
  and prediction ``difficulty`` (`score` 0–1 higher=harder, `band` 1–5). Admin
  ``GET /admin/listmatches`` / ``GET /admin/getmatch`` include the same field.
  Difficulty is persisted on the match and frozen after first compute / selection
  (later FINISHED ingest does not recalculate).
- `POST /user/addprediction` / `GET /user/getprediction?gameId=` — player submissions
  (JWT role `player`, aud=`superiorsix`; needs an open round + selected match; 10m cutoff)
- Admin predictions (JWT `admin`; SS-17): `GET /admin/listpredictionsnew` —
  flat `predictions` (SS-27 / `?matchdate=dd-mm-yyyy`) plus `players` with
  `prediction_count` / slate size (`subroundId` or current open subround),
  `GET /admin/listpredictions?authId=`, `POST /admin/addprediction` /
  `POST /admin/addpredictions` (on behalf of a player; cutoff not enforced,
  legacy parity), `GET /admin/dropprediction?id=` (soft-drop)
- Roster (proxied to baked-user by auth `sub`):
  `GET /admin/listplayers`, `POST /admin/inviteplayer`, `POST /admin/addplayer`,
  `GET /admin/dropplayer?authId=`, `GET /admin/reactivateplayer?authId=`,
  `POST /user/updatedetails`
- Predictions require JWT `player` **and** an active baked-user roster profile
  (invite accepted). Uninvited callers get 403 even with a valid token.

Fixture/score ingest is **process-driven** via `MatchIngestService` and CLI
extractors (not user REST). Admin can open rounds, list leagues/matches, and
select slots with `POST /admin/addmatch`. Other legacy routes remain no-ops for
now.

### Creating a player (invite → set-password claim → accept)

Superior Six is **invite-only** at the roster layer (baked-user). baked-auth
provides invite-ready (passwordless) users + set-password claim (SS-66). Roles
are chosen and membership is granted **at invite**; the invitee claims a
password, then accepts the roster invite.

1. As superiorsix `admin`, invite an email + roles:
   `POST /admin/inviteplayer`
   `{"email":"martin@example.com","first_name":"…","last_name":"…","roles":["player"]}`
   Selectable: `player`, `worldcup`, `euros`.
   Superiorsix `admin` is **not** assignable here (bootstrap / direct auth only).
   `user-profile` is **always granted** server-side (not a selectable chip).
   BFF (server-side `SUPERIORSIX_AUTH_ADMIN_*`, `aud=auth`):
   `POST` baked-auth `/v1/users` with membership → then baked-user roster invite.
   Edit rostered roles later via `GET /admin/getplayerroles?email=` and
   `POST /admin/updateroles` `{"email","roles"}` (preserves existing `admin`).
2. Invitee claims password: `POST` baked-auth `/v1/auth/set-password`
   `{"email":"martin@example.com","password":"…"}` (no token returned). `/token` is **403**
   until this succeeds.
3. Invitee accepts roster: `POST /user/acceptinvite`
   `{"email":"martin@example.com","password":"…"}`.
   BFF logs the invitee in (`audience=superiorsix`) and creates the roster
   profile (consumes invite). **No** service bootstrap password on this path.
4. Play with the returned `access_token` (or login again with `audience=superiorsix`).

`POST /admin/addplayer` remains for admin-provisioned roster create when a pending
invite already exists.

### Ingest extractors (SS-7 / SS-10 / SS-11)

| CLI | Purpose |
|-----|---------|
| `poetry run baked-superiorsix-league-extractor` | Upsert league catalog (name / code / start date) |
| `poetry run baked-superiorsix-match-extractor -l PL --from 2026-11-01 [--to 2026-11-03]` | Upsert fixtures for **one** league over an inclusive date range |
| `poetry run baked-superiorsix-match-extractor -l EL1 --live` | Upsert in-play fixtures (Goal API live feed; football-data ≈ today) |
| `poetry run baked-superiorsix-tournament-extractor` | Upsert tournament catalog (`FAC`, `EC`, `WC`) into shared `leagues` |
| `poetry run baked-superiorsix-tournament-extractor -l FAC --from 2026-01-10 [--to …]` | Upsert FAC fixtures into Super Six `matches` |
| `poetry run baked-superiorsix-tournament-extractor -l EC --from …` | Upsert EC fixtures into isolated `euros_matches` (SS-19; no seed required) |
| `poetry run baked-superiorsix-worldcup-extractor` | Upsert WC into shared `leagues` (catalog); fixtures go to `worldcup_matches` |
| `poetry run baked-superiorsix-worldcup-extractor --from 2026-06-01 [--to …]` | Pull WC fixtures into `worldcup_*` (not Super Six `matches`) |
| `poetry run baked-superiorsix-auto-score` | Smart live-score pass (selected fixtures by default) |
| `poetry run baked-superiorsix-db leagues` | List leagues in the DB (TSV) |
| `poetry run baked-superiorsix-db matches [-l PL] [-d 14-03-2026] …` | List matches with optional filters (TSV) |
| `poetry run baked-superiorsix-legacy-import validate\|load --data-dir …` | Legacy CSV import (`rounds.amount_won` optional) |
| `poetry run baked-superiorsix-purge matches --before-date YYYY-MM-DD` | Delete unreferenced old matches (`--dry-run`) |
| `poetry run baked-superiorsix-purge predictions --before-round-id N` | Delete old closed-round predictions; keep winners/pots |

Providers:

| Concern | Provider |
|---------|----------|
| League catalog (`league-extractor`) | football-data.org **v2** (England area; includes EL1/EL2) |
| Tournament catalog / fixtures (`FAC`, `EC`, `WC`) | football-data.org (England area for FAC; World area for EC/WC) |
| Match fixtures `EL1` / `EL2` | [Goal API](https://goal-api.com/documentation) (`/fixtures/date/:date`, paginated) |
| Match fixtures everything else (e.g. `PL`, `ELC`) | football-data.org v4 date range |
| `--live` for EL1/EL2 | Goal API `/fixtures/live` |
| `--live` for PL/ELC/tournaments | football-data.org `status=LIVE` (`IN_PLAY` + `PAUSED`) |
| Auto-score pulls | Reuses `MatchExtractor --live` per due league (factory by league code) |

Goal API has no date-range filter on the date endpoint, so a `--from`/`--to` window
issues one request per calendar day (with `limit`/`offset` pagination).

#### Tournament CLI

```bash
# Catalog only (default codes FAC EC WC; override with -c)
poetry run baked-superiorsix-tournament-extractor
poetry run baked-superiorsix-tournament-extractor -c FAC EC

# Fixtures for one tournament (league row must already exist — run catalog first)
poetry run baked-superiorsix-tournament-extractor -l FAC --from 2026-01-10 --to 2026-01-12
poetry run baked-superiorsix-tournament-extractor -l EC --live
```

**World Cup side game (SS-20):** isolated `worldcup_matches` /
`worldcup_predictions` / `worldcup_settings` tables (static buy-in pot; ET/pens
scoring). Player routes under `/worldcup/*` (legacy alias
`/qatarhero/listpredictions`); admin under `/admin/worldcup/*`. Role gate: JWT
`worldcup`. Use `baked-superiorsix-worldcup-extractor` for WC fixtures — do
**not** rely on `tournament-extractor -l WC` for the side game (that path still
writes shared `matches`). Euros side game remains SS-19. No Flashscore.

#### Auto-score (SS-11)

Standalone process — **do not** put cron/K8s in this repo; schedule externally
(~every 15 minutes is fine). Each run:

1. Loads scoreable fixtures in the live-candidate window
   (`match_date` within **1 hour before → 4 hours after** kickoff relative to
   now; default: Super Six selections with `use_match`; pass `--all-fixtures`
   for everything).
2. Skips API work when:
   - **before active** — `now < match_date + active_offset` (default
     `DEFAULT_ACTIVE_OFFSET = +2 minutes`; positive = after kickoff, negative = before)
   - **half-time pause (15 min)** — concrete HT from the provider, or inferred at
     `match_date + 48 minutes` (45 + 3 added time)
   - **recheck wait** — `now < last_updated + 2 minutes` while live
3. For leagues that still have due fixtures, pulls once via
   `MatchExtractor(..., live=True)` (stamps `last_updated` on writes).

Half-time detection by provider:

| Provider | Concrete HT signal | Domain status |
|----------|--------------------|---------------|
| Goal API (EL1/EL2) | `matchStatus=HALF_TIME` | `HALF TIME` |
| football-data.org | `status=PAUSED` | `HALF TIME` |

If the feed has not reported HT yet, auto-score still pauses from
`kickoff + 48m` for 15 minutes so EL1/EL2 stay under free-tier request budgets.

```bash
poetry run baked-superiorsix-auto-score
poetry run baked-superiorsix-auto-score --all-fixtures
poetry run baked-superiorsix-auto-score -s 7200   # optional legacy-style loop budget
```

Env vars (also accept `SUPERIORSIX_` prefix):

- `FOOTBALL_DATA_API_TOKEN` — football-data.org `X-Auth-Token` (optional for league list; required for PL/ELC/tournament match extract)
- `GOAL_API_KEY` — Goal API bearer token (EL1/EL2 match extract + auto-score)

No Flashscore / Selenium. Match extract may still apply scores when the fixture
payload already includes them (`--live` / auto-score is the near-real-time path).

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
Set `SUPERIORSIX_JWT_AUDIENCE=superiorsix` so bearer tokens must carry matching
JWT `aud`. Roles are app-scoped and lowercase: `player`, `admin`,
`user-profile`, `euros`, `worldcup`. Request tokens from baked-auth
with `audience=superiorsix` after a membership has been granted.

Point `SUPERIORSIX_USER_SERVICE_URL` at baked-user (default `http://127.0.0.1:8001`)
so roster / display-name calls resolve profiles by auth `sub`.

## Run

```bash
poetry run baked-superiorsix
```

OpenAPI docs are at [http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs) for poetry, or `:8030` when using Docker.

- Health: `GET /health`

### Full backend stack (local Mac / Linux)

Entry point for auth + user + superiorsix together (shared JWT, SQLite volumes,
bootstrap + invite notes):

```bash
cd local-dev
cp .env.example .env   # set BAKED_AUTH_CLIENT_GIT_TOKEN
docker compose up --build
```

See [`local-dev/README.md`](./local-dev/README.md). Host ports: **8010** / **8020** / **8030**.
After the stack is healthy, `./seed-dev-data.sh` bootstraps ops, creates three
players, and runs league + PL/ELC match extract for 2026-10-31…2026-11-07.
Front end: sibling [`baked-superiorsix-app`](https://github.com/MartianBerk/baked-superiorsix-app) (host Expo).

## Tests

```bash
poetry run pytest
```

## Route map

Legacy subdomain → path prefix: `game`, `user`, `admin`, `stats`, `euros`,
`worldcup`, plus legacy aliases `eurowizard` and `qatarhero` where the old app
used alternate subdomains for the same handlers.

**Euros (SS-19):** `/euros/*` (+ `/eurowizard/listpredictions`) and
`/admin/euros/*` are live against isolated `euros_*` tables. Predictions require
the `euros` JWT role. **World Cup (SS-20):** `/worldcup/*` and
`/admin/worldcup/*` against `worldcup_*` tables; JWT role `worldcup`.
