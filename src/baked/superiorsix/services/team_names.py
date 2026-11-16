"""Normalize provider team spellings for stats identity (league table / form)."""

from __future__ import annotations

# Trailing club suffixes football-data.org appends; order matters (longer first).
_TRAILING_SUFFIXES = (" AFC", " FC")

# Flashscore-style short / alternate spellings → preferred display / stats key.
# First entry in each group is the canonical label (football-data long form
# without trailing FC/AFC). Suffix-strip alone cannot merge these pairs;
# SS-260 left them as split table rows (e.g. Coventry vs Coventry City → pos 30).
_HARD_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("Coventry City", "Coventry"),
    ("Newcastle United", "Newcastle"),
    ("Tottenham Hotspur", "Tottenham", "Spurs"),
    ("Hull City", "Hull"),
    ("Manchester United", "Man Utd", "Man United"),
    ("Manchester City", "Man City"),
    ("West Ham United", "West Ham"),
    ("Leeds United", "Leeds"),
    ("Brighton & Hove Albion", "Brighton", "Brighton and Hove Albion"),
    ("Nottingham Forest", "Nottm Forest", "Nott'm Forest"),
    ("Wolverhampton Wanderers", "Wolves"),
    ("Leicester City", "Leicester"),
    ("Norwich City", "Norwich"),
    ("Ipswich Town", "Ipswich"),
    ("AFC Bournemouth", "Bournemouth"),
    ("Sheffield United", "Sheffield Utd"),
    ("Sheffield Wednesday", "Sheffield Wed"),
    ("West Bromwich Albion", "West Brom"),
    ("Queens Park Rangers", "QPR"),
    ("Birmingham City", "Birmingham"),
    ("Cardiff City", "Cardiff"),
    ("Swansea City", "Swansea"),
    ("Stoke City", "Stoke"),
    ("Exeter City", "Exeter"),
    ("Lincoln City", "Lincoln"),
    ("Charlton Athletic", "Charlton"),
    ("Bolton Wanderers", "Bolton"),
    ("Blackburn Rovers", "Blackburn"),
    ("Derby County", "Derby"),
    ("Preston North End", "Preston"),
    ("Huddersfield Town", "Huddersfield"),
    ("Oxford United", "Oxford Utd"),
    ("Peterborough United", "Peterborough"),
    ("Rotherham United", "Rotherham"),
    ("Wigan Athletic", "Wigan"),
    ("Plymouth Argyle", "Plymouth"),
    ("Wycombe Wanderers", "Wycombe"),
    ("Milton Keynes Dons", "MK Dons"),
    ("Cambridge United", "Cambridge Utd"),
    ("Northampton Town", "Northampton"),
    ("Mansfield Town", "Mansfield"),
    ("Crawley Town", "Crawley"),
    ("Stockport County", "Stockport"),
    ("Bradford City", "Bradford"),
    ("Burton Albion", "Burton"),
    ("Doncaster Rovers", "Doncaster"),
    ("Shrewsbury Town", "Shrewsbury"),
    ("Leyton Orient", "Orient"),
    ("Port Vale",),
)


def _strip_trailing_club_suffix(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        return cleaned
    upper = cleaned.upper()
    for suffix in _TRAILING_SUFFIXES:
        if upper.endswith(suffix) and len(cleaned) > len(suffix):
            candidate = cleaned[: -len(suffix)].strip()
            if candidate:
                return candidate
    return cleaned


def _build_hard_alias_maps() -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """casefold lookup → canonical, and canonical.casefold → all group spellings."""
    to_canonical: dict[str, str] = {}
    group_members: dict[str, frozenset[str]] = {}
    for group in _HARD_ALIAS_GROUPS:
        canonical = group[0]
        members = frozenset(group)
        group_members[canonical.casefold()] = members
        for spelling in group:
            to_canonical[spelling.casefold()] = canonical
    return to_canonical, group_members


_HARD_ALIAS_LOOKUP, _HARD_ALIAS_MEMBERS = _build_hard_alias_maps()


def normalize_team_name(name: str) -> str:
    """Collapse common provider variants for table / alias identity.

    1. Strip a trailing `` FC`` or `` AFC`` (e.g. ``Arsenal FC`` → ``Arsenal``,
       ``Sunderland AFC`` → ``Sunderland``). Leading ``AFC`` names such as
       ``AFC Bournemouth`` are unchanged by this step.
    2. Apply hard Flashscore ↔ football-data aliases (e.g. ``Coventry`` →
       ``Coventry City``, ``Man Utd`` → ``Manchester United``).

    Surrounding whitespace is trimmed. Stored match rows keep original API
    spellings; this is read-path only.
    """
    cleaned = _strip_trailing_club_suffix(name)
    if not cleaned:
        return cleaned
    return _HARD_ALIAS_LOOKUP.get(cleaned.casefold(), cleaned)


def team_name_variants(name: str) -> frozenset[str]:
    """Raw name plus normalized base, hard-alias spellings, and FC/AFC forms."""
    stripped = name.strip()
    base = normalize_team_name(name)
    variants: set[str] = {stripped, base, _strip_trailing_club_suffix(stripped)}
    members = _HARD_ALIAS_MEMBERS.get(base.casefold(), frozenset({base}))
    variants.update(members)
    expanded = set(variants)
    for spelling in variants:
        if not spelling:
            continue
        expanded.add(f"{spelling} FC")
        expanded.add(f"{spelling} AFC")
        # Short Flashscore forms also appear with FC in some feeds.
        stripped_spelling = _strip_trailing_club_suffix(spelling)
        if stripped_spelling and stripped_spelling != spelling:
            expanded.add(stripped_spelling)
            expanded.add(f"{stripped_spelling} FC")
            expanded.add(f"{stripped_spelling} AFC")
    return frozenset(v for v in expanded if v)
