#!/usr/bin/env bash
# Seed local-dev Superior Six with three player accounts + PL/ELC fixtures.
#
# Prerequisites:
#   cd baked-superiorsix/local-dev
#   docker compose up --build -d   # (or already running)
#   FOOTBALL_DATA_API_TOKEN set in .env for match/league extract
#
# Usage:
#   ./seed-dev-data.sh
#
# Idempotent where practical: skips emails already on the roster; extractors upsert.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Prefer host-published ports (works whether compose runs in this shell or not).
AUTH_URL="${AUTH_URL:-http://127.0.0.1:8010}"
SUPERSIX_URL="${SUPERSIX_URL:-http://127.0.0.1:8030}"

# Defaults match local-dev/.env.example
OPS_EMAIL="${AUTH_BOOTSTRAP_EMAIL:-ops@example.com}"
OPS_PASSWORD="${AUTH_BOOTSTRAP_PASSWORD:-local-dev-change-me}"
PLAYER_PASSWORD="${SEED_PLAYER_PASSWORD:-local-dev-player}"

# Inclusive fixture window (UTC). Override with SEED_FROM / SEED_TO.
DATE_FROM="${SEED_FROM:-2026-10-31}"
DATE_TO="${SEED_TO:-2026-11-07}"
LEAGUES="${SEED_LEAGUES:-PL ELC}"

# name|email|first|last|nickname
PLAYERS=(
  "alice|alice@example.com|Alice|Player|Alice"
  "bob|bob@example.com|Bob|Player|Bobby"
  "cara|cara@example.com|Cara|Player|Cara"
)

log() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

json_get() {
  # json_get <json> <python-expr using `data`>
  local raw="$1"
  local expr="$2"
  python3 -c "import json,sys; data=json.loads(sys.argv[1]); print($expr)" "$raw"
}

wait_healthy() {
  local url="$1"
  local name="$2"
  local i
  for i in $(seq 1 60); do
    if curl -sf "$url/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "$name not healthy at $url (is docker compose up?)"
}

load_dotenv() {
  if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    OPS_EMAIL="${AUTH_BOOTSTRAP_EMAIL:-$OPS_EMAIL}"
    OPS_PASSWORD="${AUTH_BOOTSTRAP_PASSWORD:-$OPS_PASSWORD}"
  fi
}

auth_token() {
  local email="$1"
  local password="$2"
  local audience="$3"
  local resp
  resp="$(curl -sf -X POST "$AUTH_URL/v1/auth/token" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "email=$email" \
    --data-urlencode "password=$password" \
    --data-urlencode "audience=$audience")" \
    || die "login failed for $email (audience=$audience)"
  json_get "$resp" "data['access_token']"
}

roster_has_email() {
  local token="$1"
  local email="$2"
  local resp
  resp="$(curl -sf "$SUPERSIX_URL/admin/listplayers" \
    -H "Authorization: Bearer $token")" \
    || die "listplayers failed"
  python3 -c "
import json, sys
email = sys.argv[1].strip().lower()
players = json.loads(sys.argv[2]).get('players') or []
raise SystemExit(0 if any((p.get('email') or '').strip().lower() == email for p in players) else 1)
" "$email" "$resp"
}

seed_player() {
  local token="$1"
  local email="$2"
  local first="$3"
  local last="$4"
  local nick="$5"
  local http body code

  if roster_has_email "$token" "$email"; then
    log "skip $email (already on roster)"
    return 0
  fi

  log "invite $email"
  body="$(curl -sS -w '\n%{http_code}' -X POST "$SUPERSIX_URL/admin/inviteplayer" \
    -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json,sys
print(json.dumps({
  'email': sys.argv[1],
  'first_name': sys.argv[2],
  'last_name': sys.argv[3],
  'nickname': sys.argv[4],
  'roles': ['player'],
}))
" "$email" "$first" "$last" "$nick")")"
  code="${body##*$'\n'}"
  http="${body%$'\n'*}"
  if [[ "$code" != "201" && "$code" != "200" ]]; then
    # Pending re-invite is fine; accepted duplicate means skip remaining steps.
    if python3 -c "
import json,sys
detail=str(json.loads(sys.argv[1]).get('detail','')).lower()
raise SystemExit(0 if 'already accepted' in detail or 'already exists' in detail else 1)
" "$http" 2>/dev/null; then
      log "skip $email (invite already accepted)"
      return 0
    fi
    die "inviteplayer failed ($code): $http"
  fi

  log "set-password $email"
  body="$(curl -sS -w '\n%{http_code}' -X POST "$AUTH_URL/v1/auth/set-password" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json,sys
print(json.dumps({'email': sys.argv[1], 'password': sys.argv[2]}))
" "$email" "$PLAYER_PASSWORD")")"
  code="${body##*$'\n'}"
  http="${body%$'\n'*}"
  if [[ "$code" != "200" ]]; then
    if [[ "$code" == "409" ]]; then
      log "password already set for $email"
    else
      die "set-password failed ($code): $http"
    fi
  fi

  log "acceptinvite $email"
  body="$(curl -sS -w '\n%{http_code}' -X POST "$SUPERSIX_URL/user/acceptinvite" \
    -H 'Content-Type: application/json' \
    -d "$(python3 -c "
import json,sys
print(json.dumps({
  'email': sys.argv[1],
  'password': sys.argv[2],
  'first_name': sys.argv[3],
  'last_name': sys.argv[4],
  'nickname': sys.argv[5],
}))
" "$email" "$PLAYER_PASSWORD" "$first" "$last" "$nick")")"
  code="${body##*$'\n'}"
  http="${body%$'\n'*}"
  if [[ "$code" != "201" ]]; then
    if [[ "$code" == "409" ]]; then
      log "roster already exists for $email"
      return 0
    fi
    die "acceptinvite failed ($code): $http"
  fi
  log "created player $email"
}

run_extractors() {
  log "league extractor"
  docker compose exec -T baked-superiorsix baked-superiorsix-league-extractor

  local league
  for league in $LEAGUES; do
    log "match extractor league=$league from=$DATE_FROM to=$DATE_TO"
    docker compose exec -T baked-superiorsix \
      baked-superiorsix-match-extractor -l "$league" --from "$DATE_FROM" --to "$DATE_TO"
  done
}

main() {
  need_cmd curl
  need_cmd python3
  need_cmd docker

  load_dotenv

  if [[ -z "${FOOTBALL_DATA_API_TOKEN:-}" ]]; then
    warn "FOOTBALL_DATA_API_TOKEN is empty — league/match extract will likely fail"
  fi

  wait_healthy "$AUTH_URL" "baked-auth"
  wait_healthy "$SUPERSIX_URL" "baked-superiorsix"

  log "bootstrap auth admin (+ superiorsix membership)"
  docker compose exec -T baked-auth baked-auth-bootstrap

  log "login ops ($OPS_EMAIL, audience=superiorsix)"
  local ops_token
  ops_token="$(auth_token "$OPS_EMAIL" "$OPS_PASSWORD" superiorsix)"

  local entry _name email first last nick
  for entry in "${PLAYERS[@]}"; do
    IFS='|' read -r _name email first last nick <<<"$entry"
    seed_player "$ops_token" "$email" "$first" "$last" "$nick"
  done

  run_extractors

  log "done"
  cat <<EOF

Seeded players (password: $PLAYER_PASSWORD):
  alice@example.com
  bob@example.com
  cara@example.com

Fixtures: leagues [$LEAGUES] from $DATE_FROM to $DATE_TO (upsert).
EOF
}

main "$@"
