# Legacy DB CSV import

Place old-database `SELECT *` exports here, then run:

```bash
export SUPERIORSIX_DATABASE_URL='sqlite+aiosqlite:///./baked_superiorsix.db'

poetry run baked-superiorsix-legacy-import validate --data-dir ./migration-data
poetry run baked-superiorsix-legacy-import load --data-dir ./migration-data --truncate
```

Run `baked-superiorsix-league-extractor` against the same database **before** `load` so `leagues` rows exist; use `league_id_map.csv` to remap old `matches.league_id` values.

## Required files

| File | Purpose |
|------|---------|
| `player_mapping.csv` | `old_player_id` → `auth_id` (UUID) |
| `league_id_map.csv` | `old_league_id` → league `code` (must exist in DB) |
| `rounds.csv` | Parent pot rounds |
| `matches.csv` | All fixtures |
| `predictions.csv` | Player picks (`player_id` transformed via mapping) |
| `round_winners.csv` | Closed-round winners (`player_id` → `auth_id`) |

## Optional files

| File | Purpose |
|------|---------|
| `subrounds.csv` | Admin slates for recent rounds |
| `subround_match_slots.csv` | `match_id`, `subround_id`, `game_number`, `use_match` |

## Header examples

```csv
old_player_id,auth_id
1,aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
```

```csv
old_league_id,code
1,PL
2,ELC
```

```csv
id,start_date,end_date,buy_in_pence
1,2024-08-01,2025-05-31,500
```

```csv
id,league_id,external_id,match_date,status,home_team,away_team,use_match,home_score,away_score,game_number,subround_id
```

```csv
id,round_id,player_id,match_id,prediction,dropped
```

```csv
round_id,player_id
```

```csv
id,round_id,sequence,created_at,closed_at
```

```csv
match_id,subround_id,game_number,use_match
```

Column names may use legacy aliases (`buy_in`, `football_data_id`, etc.); the importer normalizes them.

## Post-import verification

1. Row counts printed by `load` (compare to CSV line counts).
2. `poetry run baked-superiorsix-db leagues` and `poetry run baked-superiorsix-db matches --status FINISHED`
3. With the API running and roster seeded in baked-user:
   - `GET /stats/winners` — closed rounds archive
   - `GET /stats/aggregate` — previous closed round success rates
   - `GET /admin/historicrounds`
   - Spot-check `GET /admin/listpredictionsnew?matchdate=dd-mm-yyyy`

### Stats gotchas

- `predictions.csv` must keep the legacy ``drop`` flag (also accepted as ``dropped``). Dropped picks are excluded from Performance rates.
- Closed-round matches need ``use_match=1`` and ``status=FINISHED`` (with scores) or Performance stays empty while Winners may still list rounds.
- `player_mapping.csv` ``auth_id`` values should match baked-user roster ``auth_id``s so Winners/Performance show names instead of UUID strings. Rates still compute when profiles are missing; known inactive profiles are excluded.

See `poetry run baked-superiorsix-legacy-import --help` for flags (`--truncate`, `--dry-run`).
