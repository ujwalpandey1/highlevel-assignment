from datetime import UTC, datetime

# Seed, demo and evaluation reference. Interactive requests capture their own time.
INTERPRETATION_TIME = "2026-09-28T12:00:00Z"


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("A clock must include a timezone")
    return parsed.astimezone(UTC)


def iso(value: datetime) -> str:
    # ISO formatting avoids locale/timezone system calls on every seeded row.
    # Keep canonical UTC seconds so lexical SQL comparisons remain chronological.
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(UTC)
