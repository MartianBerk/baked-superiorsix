from __future__ import annotations

from datetime import date
from uuid import UUID

from baked.superiorsix.clients.auth_admin import AuthAdminGateway
from baked.superiorsix.clients.user_profiles import UserProfileGateway
from baked.superiorsix.core.config import Settings
from baked.superiorsix.domain.exceptions import (
    AuthAdminError,
    RosterError,
    RoundError,
    RoundNotFoundError,
)
from baked.superiorsix.domain.models import Round
from baked.superiorsix.repositories.rounds import RoundRepository


class RoundService:
    def __init__(
        self,
        rounds: RoundRepository,
        *,
        profiles: UserProfileGateway | None = None,
        auth_admin: AuthAdminGateway | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._rounds = rounds
        self._profiles = profiles
        self._auth_admin = auth_admin
        self._settings = settings

    async def get(self, round_id: int) -> Round:
        round_ = await self._rounds.get(round_id)
        if round_ is None or round_.id is None:
            raise RoundNotFoundError(f"Round {round_id} not found.")
        return round_

    async def current(self) -> Round:
        round_ = await self._rounds.current()
        if round_ is None or round_.id is None:
            raise RoundNotFoundError("No round in progress.")
        return round_

    async def current_or_none(self) -> Round | None:
        return await self._rounds.current()

    async def list(self) -> list[Round]:
        return await self._rounds.list()

    async def open_round(self, *, start_date: date, buy_in_pence: int) -> Round:
        """Open a new round. Fails if one is already in progress."""
        if await self._rounds.current() is not None:
            raise RoundError("Round already in progress.")
        if buy_in_pence < 0:
            raise RoundError("buy_in_pence must be >= 0.")
        return await self._rounds.add(Round(start_date=start_date, buy_in_pence=buy_in_pence))

    async def end_current(self, end_date: date, winner_ids: list[UUID]) -> Round:
        """Close the open round with one or more winners (roster auth ids)."""
        if not winner_ids:
            raise RoundError("at least one winner_id is required")
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for auth_id in winner_ids:
            if auth_id in seen:
                raise RoundError("winner_ids must be unique")
            seen.add(auth_id)
            unique.append(auth_id)
        round_ = await self.current()
        round_.end_date = end_date
        round_.n_players = await self._active_roster_count()
        return await self._rounds.end_with_winners(round_, unique)

    async def _active_roster_count(self) -> int:
        """Same source as gameday meta ``n_players`` (active baked-user roster)."""
        if self._profiles is None or self._auth_admin is None or self._settings is None:
            return 0
        email = self._settings.auth_admin_email
        password = self._settings.auth_admin_password
        if not email or password is None:
            return 0
        try:
            token = await self._auth_admin.login_user(
                email=email,
                password=password.get_secret_value(),
                audience=self._settings.jwt_audience,
            )
            roster = await self._profiles.list_profiles(token, active=True)
        except (AuthAdminError, RosterError):
            return 0
        return len(roster)
