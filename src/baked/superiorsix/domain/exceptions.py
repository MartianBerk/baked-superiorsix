class SuperiorSixError(Exception):
    """Base error for the Superior Six game service."""


class AuthenticationError(SuperiorSixError):
    """Raised when a baked-auth access token is invalid."""


class MatchNotFoundError(SuperiorSixError):
    """Raised when a match cannot be found."""


class LeagueNotFoundError(SuperiorSixError):
    """Raised when a league cannot be found."""


class MatchSelectionError(SuperiorSixError):
    """Raised when selecting a Super Six match slot fails."""
