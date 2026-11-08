class SuperiorSixError(Exception):
    """Base error for the Superior Six game service."""


class MatchNotFoundError(SuperiorSixError):
    """Raised when a match cannot be found."""


class LeagueNotFoundError(SuperiorSixError):
    """Raised when a league cannot be found."""


class MatchSelectionError(SuperiorSixError):
    """Raised when selecting a Super Six match slot fails."""


class RoundNotFoundError(SuperiorSixError):
    """Raised when no open round is available."""


class RoundError(SuperiorSixError):
    """Raised when opening or closing a round fails."""


class SubroundNotFoundError(SuperiorSixError):
    """Raised when a prediction subround cannot be found."""


class SubroundError(SuperiorSixError):
    """Raised when opening or resolving a prediction subround fails."""


class PredictionError(SuperiorSixError):
    """Raised when a prediction cannot be accepted."""


class RosterError(SuperiorSixError):
    """Raised when a baked-user roster call fails."""


class RosterNotFoundError(RosterError):
    """Raised when a roster profile cannot be found in baked-user."""


class NotOnRosterError(RosterError):
    """Raised when the caller is not an active invited roster player."""


class AuthAdminError(SuperiorSixError):
    """Raised when a server-side baked-auth admin call fails."""


class EurosTournamentNotFoundError(SuperiorSixError):
    """Raised when no open Euro Wizard tournament is available."""


class EurosTournamentError(SuperiorSixError):
    """Raised when opening or closing a Euro Wizard tournament fails."""


class EurosMatchNotFoundError(SuperiorSixError):
    """Raised when a Euro Wizard match cannot be found."""


class EurosPredictionError(SuperiorSixError):
    """Raised when a Euro Wizard prediction cannot be accepted."""


class WorldcupMatchNotFoundError(SuperiorSixError):
    """Raised when a World Cup side-game match cannot be found."""


class WorldcupError(SuperiorSixError):
    """Raised when a World Cup side-game operation fails."""

