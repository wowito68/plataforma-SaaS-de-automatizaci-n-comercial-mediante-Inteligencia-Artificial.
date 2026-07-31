from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import uuid6


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self) -> UUID: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid7Generator:
    def new(self) -> UUID:
        return uuid6.uuid7()


ClockFactory = Callable[[], datetime]
