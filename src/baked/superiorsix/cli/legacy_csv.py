"""Parse and validate legacy Superior Six CSV exports for DB import."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

REQUIRED_FILES = (
    "player_mapping.csv",
    "league_id_map.csv",
    "rounds.csv",
    "matches.csv",
    "predictions.csv",
    "round_winners.csv",
)

OPTIONAL_FILES = (
    "subrounds.csv",
    "subround_match_slots.csv",
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        rows: list[dict[str, str]] = []
        for raw in reader:
            normalized: dict[str, str] = {}
            for key, value in raw.items():
                if key is None:
                    continue
                normalized[_normalize_key(key)] = (value or "").strip()
            rows.append(normalized)
        return rows


def pick(row: dict[str, str], *names: str) -> str | None:
    for name in names:
        key = _normalize_key(name)
        if key in row and row[key] != "":
            return row[key]
    return None


def parse_int(value: str | None, *, label: str) -> int:
    if value is None or value == "":
        raise ValueError(f"{label}: missing integer")
    return int(value)


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    lowered = value.casefold()
    if lowered in {"1", "true", "t", "yes", "y"}:
        return True
    if lowered in {"0", "false", "f", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean {value!r}")


def parse_uuid(value: str, *, label: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid UUID {value!r}") from exc


def parse_date(value: str | None, *, label: str) -> date:
    if value is None or value == "":
        raise ValueError(f"{label}: missing date")
    text = value[:10]
    if "T" in value or " " in value.strip():
        return parse_datetime(value, label=label).date()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{label}: cannot parse date {value!r}")


def parse_optional_date(value: str | None, *, label: str) -> date | None:
    if value is None or value == "":
        return None
    return parse_date(value, label=label)


def parse_datetime(value: str | None, *, label: str) -> datetime:
    if value is None or value == "":
        raise ValueError(f"{label}: missing datetime")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d-%m-%Y %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"{label}: cannot parse datetime {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_optional_datetime(value: str | None, *, label: str) -> datetime | None:
    if value is None or value == "":
        return None
    return parse_datetime(value, label=label)


def normalize_prediction(value: str | None) -> str:
    if value is None or value == "":
        raise ValueError("missing prediction")
    lowered = value.strip().casefold()
    aliases = {
        "h": "home",
        "home": "home",
        "a": "away",
        "away": "away",
        "d": "draw",
        "draw": "draw",
        "void": "void",
    }
    if lowered not in aliases:
        raise ValueError(f"invalid prediction {value!r}")
    return aliases[lowered]


def normalize_match_status(value: str | None) -> str:
    """Map CSV status strings onto domain ``MatchStatus`` values."""
    raw = (value or "SCHEDULED").strip()
    if not raw:
        return "SCHEDULED"
    key = raw.casefold().replace("_", " ").replace("-", " ")
    aliases = {
        "scheduled": "SCHEDULED",
        "postponed": "POSTPONED",
        "in play": "IN PLAY",
        "inplay": "IN PLAY",
        "live": "IN PLAY",
        "half time": "HALF TIME",
        "halftime": "HALF TIME",
        "ht": "HALF TIME",
        "finished": "FINISHED",
        "ft": "FINISHED",
        "full time": "FINISHED",
    }
    return aliases.get(key, raw.upper())


@dataclass(frozen=True, slots=True)
class PlayerMapping:
    old_player_id: int
    auth_id: UUID


@dataclass(frozen=True, slots=True)
class LeagueIdMapRow:
    old_league_id: int
    code: str


@dataclass(frozen=True, slots=True)
class RoundRow:
    id: int
    start_date: date
    end_date: date | None
    buy_in_pence: int


@dataclass(frozen=True, slots=True)
class SubroundRow:
    id: int
    round_id: int
    sequence: int
    created_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MatchRow:
    id: int
    old_league_id: int
    external_id: str
    match_date: datetime
    status: str
    home_team: str
    away_team: str
    use_match: bool
    home_score: int | None
    away_score: int | None
    game_number: int | None
    subround_id: int | None
    match_minute: int | None
    difficulty_score: float | None
    difficulty_band: int | None
    last_updated: datetime


@dataclass(frozen=True, slots=True)
class PredictionRow:
    id: int | None
    round_id: int
    auth_id: UUID
    match_id: int
    prediction: str
    dropped: bool


@dataclass(frozen=True, slots=True)
class RoundWinnerRow:
    round_id: int
    auth_id: UUID


@dataclass(frozen=True, slots=True)
class SubroundMatchSlotRow:
    match_id: int
    subround_id: int
    game_number: int | None
    use_match: bool


@dataclass
class LegacyCsvBundle:
    data_dir: Path
    player_mapping: dict[int, UUID] = field(default_factory=dict)
    league_id_map: dict[int, str] = field(default_factory=dict)
    rounds: list[RoundRow] = field(default_factory=list)
    subrounds: list[SubroundRow] = field(default_factory=list)
    matches: list[MatchRow] = field(default_factory=list)
    predictions: list[PredictionRow] = field(default_factory=list)
    round_winners: list[RoundWinnerRow] = field(default_factory=list)
    subround_match_slots: list[SubroundMatchSlotRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _err(bundle: LegacyCsvBundle, message: str) -> None:
    bundle.errors.append(message)


def load_bundle(data_dir: Path) -> LegacyCsvBundle:
    bundle = LegacyCsvBundle(data_dir=data_dir)
    missing = [name for name in REQUIRED_FILES if not (data_dir / name).exists()]
    if missing:
        for name in missing:
            _err(bundle, f"missing required file: {name}")
        return bundle

    try:
        _load_player_mapping(bundle)
        _load_league_id_map(bundle)
        _load_rounds(bundle)
        _load_subrounds(bundle)
        _load_matches(bundle)
        _load_predictions(bundle)
        _load_round_winners(bundle)
        _load_subround_match_slots(bundle)
        _cross_validate(bundle)
    except (OSError, csv.Error) as exc:
        _err(bundle, f"unexpected parse failure: {exc}")
    return bundle


def _load_player_mapping(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "player_mapping.csv"), start=2):
        try:
            old_id = parse_int(pick(row, "old_player_id", "player_id"), label="old_player_id")
            auth = parse_uuid(
                pick(row, "auth_id", "new_auth_id") or "",
                label="auth_id",
            )
            if old_id in bundle.player_mapping:
                _err(bundle, f"player_mapping.csv:{index}: duplicate old_player_id {old_id}")
            bundle.player_mapping[old_id] = auth
        except ValueError as exc:
            _err(bundle, f"player_mapping.csv:{index}: {exc}")


def _load_league_id_map(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "league_id_map.csv"), start=2):
        try:
            old_id = parse_int(pick(row, "old_league_id", "league_id"), label="old_league_id")
            code = pick(row, "code", "league_code") or ""
            if not code:
                raise ValueError("missing league code")
            bundle.league_id_map[old_id] = code.upper()
        except ValueError as exc:
            _err(bundle, f"league_id_map.csv:{index}: {exc}")


def _load_rounds(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "rounds.csv"), start=2):
        try:
            bundle.rounds.append(
                RoundRow(
                    id=parse_int(pick(row, "id"), label="id"),
                    start_date=parse_date(pick(row, "start_date"), label="start_date"),
                    end_date=parse_optional_date(pick(row, "end_date"), label="end_date"),
                    buy_in_pence=parse_int(
                        pick(row, "buy_in_pence", "buy_in", "buyin_pence"),
                        label="buy_in_pence",
                    ),
                )
            )
        except ValueError as exc:
            _err(bundle, f"rounds.csv:{index}: {exc}")


def _load_subrounds(bundle: LegacyCsvBundle) -> None:
    path = bundle.data_dir / "subrounds.csv"
    if not path.exists():
        return
    for index, row in enumerate(read_csv(path), start=2):
        try:
            subround_id = parse_int(pick(row, "id", "subround_id"), label="id")
            bundle.subrounds.append(
                SubroundRow(
                    id=subround_id,
                    round_id=parse_int(pick(row, "round_id"), label="round_id"),
                    sequence=parse_int(
                        pick(row, "sequence", "subround_sequence"),
                        label="sequence",
                    ),
                    created_at=parse_datetime(
                        pick(row, "created_at", "created"),
                        label="created_at",
                    ),
                    closed_at=parse_optional_datetime(
                        pick(row, "closed_at", "closed"),
                        label="closed_at",
                    ),
                )
            )
        except ValueError as exc:
            _err(bundle, f"subrounds.csv:{index}: {exc}")


def _load_matches(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "matches.csv"), start=2):
        try:
            match_id = parse_int(pick(row, "id"), label="id")
            external = pick(row, "external_id", "football_data_id", "fd_id")
            if not external:
                external = f"legacy-match-{match_id}"
            match_date = parse_datetime(pick(row, "match_date", "date"), label="match_date")
            last_updated_raw = pick(row, "last_updated", "updated_at")
            last_updated = (
                parse_datetime(last_updated_raw, label="last_updated")
                if last_updated_raw
                else match_date
            )
            diff_raw = pick(row, "difficulty_score", "difficulty")
            difficulty_score = float(diff_raw) if diff_raw else None
            bundle.matches.append(
                MatchRow(
                    id=match_id,
                    old_league_id=parse_int(pick(row, "league_id"), label="league_id"),
                    external_id=external,
                    match_date=match_date,
                    status=normalize_match_status(pick(row, "status")),
                    home_team=pick(row, "home_team", "home") or "",
                    away_team=pick(row, "away_team", "away") or "",
                    use_match=parse_bool(pick(row, "use_match"), default=False),
                    home_score=parse_optional_int(pick(row, "home_score")),
                    away_score=parse_optional_int(pick(row, "away_score")),
                    game_number=parse_optional_int(pick(row, "game_number", "game_no")),
                    subround_id=parse_optional_int(pick(row, "subround_id")),
                    match_minute=parse_optional_int(pick(row, "match_minute", "minute")),
                    difficulty_score=difficulty_score,
                    difficulty_band=parse_optional_int(pick(row, "difficulty_band", "band")),
                    last_updated=last_updated,
                )
            )
        except ValueError as exc:
            _err(bundle, f"matches.csv:{index}: {exc}")


def _resolve_auth_id(bundle: LegacyCsvBundle, row: dict[str, str], *, label: str) -> UUID | None:
    if pick(row, "auth_id"):
        try:
            return parse_uuid(pick(row, "auth_id") or "", label=label)
        except ValueError as exc:
            _err(bundle, f"{label}: {exc}")
            return None
    player_id_raw = pick(row, "player_id", "old_player_id")
    if player_id_raw is None:
        _err(bundle, f"{label}: missing player_id / auth_id")
        return None
    try:
        player_id = int(player_id_raw)
    except ValueError:
        _err(bundle, f"{label}: invalid player_id {player_id_raw!r}")
        return None
    auth = bundle.player_mapping.get(player_id)
    if auth is None:
        _err(bundle, f"{label}: unmapped player_id {player_id}")
    return auth


def _load_predictions(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "predictions.csv"), start=2):
        label = f"predictions.csv:{index}"
        auth = _resolve_auth_id(bundle, row, label=label)
        if auth is None:
            continue
        try:
            pred_id_raw = pick(row, "id")
            bundle.predictions.append(
                PredictionRow(
                    id=int(pred_id_raw) if pred_id_raw else None,
                    round_id=parse_int(pick(row, "round_id"), label="round_id"),
                    auth_id=auth,
                    match_id=parse_int(pick(row, "match_id"), label="match_id"),
                    prediction=normalize_prediction(pick(row, "prediction", "pick")),
                    # Legacy SQLite column is ``drop``; accept ``dropped`` too.
                    dropped=parse_bool(pick(row, "dropped", "drop"), default=False),
                )
            )
        except ValueError as exc:
            _err(bundle, f"{label}: {exc}")


def _load_round_winners(bundle: LegacyCsvBundle) -> None:
    for index, row in enumerate(read_csv(bundle.data_dir / "round_winners.csv"), start=2):
        label = f"round_winners.csv:{index}"
        auth = _resolve_auth_id(bundle, row, label=label)
        if auth is None:
            continue
        try:
            bundle.round_winners.append(
                RoundWinnerRow(
                    round_id=parse_int(pick(row, "round_id"), label="round_id"),
                    auth_id=auth,
                )
            )
        except ValueError as exc:
            _err(bundle, f"{label}: {exc}")


def _load_subround_match_slots(bundle: LegacyCsvBundle) -> None:
    path = bundle.data_dir / "subround_match_slots.csv"
    if not path.exists():
        return
    for index, row in enumerate(read_csv(path), start=2):
        try:
            bundle.subround_match_slots.append(
                SubroundMatchSlotRow(
                    match_id=parse_int(pick(row, "match_id"), label="match_id"),
                    subround_id=parse_int(pick(row, "subround_id"), label="subround_id"),
                    game_number=parse_optional_int(pick(row, "game_number", "game_no")),
                    use_match=parse_bool(pick(row, "use_match"), default=True),
                )
            )
        except ValueError as exc:
            _err(bundle, f"subround_match_slots.csv:{index}: {exc}")


def _cross_validate(bundle: LegacyCsvBundle) -> None:
    round_ids = {row.id for row in bundle.rounds}
    match_ids = {row.id for row in bundle.matches}
    subround_ids = {row.id for row in bundle.subrounds}

    round_counts = Counter(row.id for row in bundle.rounds)
    round_id_dupes = [rid for rid, count in round_counts.items() if count > 1]
    for rid in round_id_dupes:
        _err(bundle, f"duplicate round id {rid}")

    match_counts = Counter(row.id for row in bundle.matches)
    match_id_dupes = [mid for mid, count in match_counts.items() if count > 1]
    for mid in match_id_dupes:
        _err(bundle, f"duplicate match id {mid}")

    external_counts = Counter(row.external_id for row in bundle.matches)
    external_dupes = [eid for eid, count in external_counts.items() if count > 1]
    for eid in external_dupes:
        _err(bundle, f"duplicate match external_id {eid!r}")

    for row in bundle.matches:
        if row.old_league_id not in bundle.league_id_map:
            _err(bundle, f"match {row.id}: league_id {row.old_league_id} not in league_id_map.csv")
        if row.subround_id is not None and row.subround_id not in subround_ids and bundle.subrounds:
            _err(bundle, f"match {row.id}: unknown subround_id {row.subround_id}")

    for row in bundle.subrounds:
        if row.round_id not in round_ids:
            _err(bundle, f"subround {row.id}: unknown round_id {row.round_id}")
    subround_pairs = Counter((row.round_id, row.sequence) for row in bundle.subrounds)
    for pair, count in subround_pairs.items():
        if count > 1:
            _err(bundle, f"duplicate subround (round_id, sequence) {pair}")

    for row in bundle.predictions:
        if row.round_id not in round_ids:
            _err(
                bundle,
                f"prediction round_id={row.round_id} match_id={row.match_id}: unknown round",
            )
        if row.match_id not in match_ids:
            _err(
                bundle,
                f"prediction round_id={row.round_id} match_id={row.match_id}: unknown match",
            )

    pred_keys = Counter((row.round_id, row.auth_id, row.match_id) for row in bundle.predictions)
    for key, count in pred_keys.items():
        if count > 1:
            _err(bundle, f"duplicate prediction (round_id, auth_id, match_id) {key}")

    for row in bundle.round_winners:
        if row.round_id not in round_ids:
            _err(bundle, f"round_winner: unknown round_id {row.round_id}")

    winner_keys = Counter((row.round_id, row.auth_id) for row in bundle.round_winners)
    for key, count in winner_keys.items():
        if count > 1:
            _err(bundle, f"duplicate round_winner {key}")

    for row in bundle.subround_match_slots:
        if row.match_id not in match_ids:
            _err(bundle, f"subround_match_slots: unknown match_id {row.match_id}")
        if row.subround_id not in subround_ids:
            _err(bundle, f"subround_match_slots: unknown subround_id {row.subround_id}")
