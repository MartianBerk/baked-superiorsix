class SuperiorSixError(Exception):
    """Base error for the Superior Six game service."""


class AuthenticationError(SuperiorSixError):
    """Raised when a baked-auth access token is invalid."""
