from datetime import date

from baked.superiorsix.domain.exceptions import RoundError, RoundNotFoundError
from baked.superiorsix.domain.models import Round
from baked.superiorsix.repositories.rounds import RoundRepository

# Default buy-in when admin match-selection bootstraps a first round on a fresh DB.
_DEFAULT_BOOTSTRAP_BUY_IN_PENCE = 0


class RoundService:
    def __init__(self, rounds: RoundRepository) -> None:
        self._rounds = rounds

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

    async def current_or_create(
        self,
        *,
        start_date: date | None = None,
        buy_in_pence: int = _DEFAULT_BOOTSTRAP_BUY_IN_PENCE,
    ) -> Round:
        """Return the open round, creating one (today / buy-in 0) if the DB has none."""
        current = await self.current_or_none()
        if current is not None:
            return current
        return await self.open_round(
            start_date=start_date or date.today(),
            buy_in_pence=buy_in_pence,
        )

    async def list(self) -> list[Round]:
        return await self._rounds.list()

    async def open_round(self, *, start_date: date, buy_in_pence: int) -> Round:
        """Open a new round. Fails if one is already in progress."""
        if await self._rounds.current() is not None:
            raise RoundError("Round already in progress.")
        if buy_in_pence < 0:
            raise RoundError("buy_in_pence must be >= 0.")
        return await self._rounds.add(Round(start_date=start_date, buy_in_pence=buy_in_pence))

    async def end_current(self, end_date: date) -> Round:
        round_ = await self.current()
        round_.end_date = end_date
        return await self._rounds.save(round_)
