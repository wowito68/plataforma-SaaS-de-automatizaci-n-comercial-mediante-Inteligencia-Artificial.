from datetime import UTC, datetime, timedelta
from uuid import UUID


class MutableClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime.now(UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class FixedIdGenerator:
    def __init__(self, value: UUID) -> None:
        self.value = value

    def new(self) -> UUID:
        return self.value
