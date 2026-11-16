"""Normalize provider team spellings for stats identity (league table / form)."""

from __future__ import annotations

# Trailing club suffixes football-data.org appends; order matters (longer first).
_TRAILING_SUFFIXES = (" AFC", " FC")


def normalize_team_name(name: str) -> str:
    """Collapse common provider variants for table / alias identity.

    Strips a trailing `` FC`` or `` AFC`` (e.g. ``Arsenal FC`` → ``Arsenal``,
    ``Sunderland AFC`` → ``Sunderland``). Leading ``AFC`` names such as
    ``AFC Bournemouth`` are unchanged. Surrounding whitespace is trimmed.
    """
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


def team_name_variants(name: str) -> frozenset[str]:
    """Raw name plus normalized base and common ``FC`` / ``AFC`` spellings."""
    base = normalize_team_name(name)
    variants = {name.strip(), base}
    if base:
        variants.add(f"{base} FC")
        variants.add(f"{base} AFC")
    return frozenset(v for v in variants if v)
