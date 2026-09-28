"""Time references survive inference/clarification and select exact database boundaries."""

import asyncio

import pytest
from conftest import FixedExtractor

from copilot.clock import INTERPRETATION_TIME, parse_time
from copilot.grounding import resolve_date
from copilot.schema import Filter
from copilot.service import Copilot

INSTRUCTION = (
    "Move open deals owned by Asha Verma from Qualified to Proposal Sent created last month."
)


@pytest.mark.parametrize(
    "phrase,clock,lower,upper",
    [
        ("yesterday", "2024-03-01T12:30:00Z", "2024-02-29T00:00:00Z", "2024-03-01T00:00:00Z"),
        ("yesterday", "2026-03-01T12:30:00Z", "2026-02-28T00:00:00Z", "2026-03-01T00:00:00Z"),
        ("yesterday", "2026-01-01T00:00:00Z", "2025-12-31T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("yesterday", "2026-03-01T01:00:00+05:30", "2026-02-27T00:00:00Z", "2026-02-28T00:00:00Z"),
        ("today", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
    ],
)
def test_relative_days_use_calendar_utc_boundaries(phrase, clock, lower, upper):
    date, question, _ = resolve_date(phrase, "created " + phrase, parse_time(clock))
    assert question is None
    assert date.model_dump() == {"field": "created_at", "gte": lower, "lt": upper}


def test_interactive_clock_is_captured_for_each_request(store, clock):
    clock.value = parse_time("2026-09-30T23:59:59Z")
    service = Copilot(store, FixedExtractor(value=None, date="last month"), wall_clock=clock)
    first = asyncio.run(service.plan("atlas", INSTRUCTION))["plan"]
    clock.advance(seconds=2)
    second = asyncio.run(service.plan("atlas", INSTRUCTION))["plan"]
    assert first["interpretation_time"] == "2026-09-30T23:59:59Z"
    assert first["filter"]["date"]["gte"] == "2026-08-01T00:00:00Z"
    assert second["interpretation_time"] == "2026-10-01T00:00:01Z"
    assert second["filter"]["date"] == {
        "field": "created_at",
        "gte": "2026-09-01T00:00:00Z",
        "lt": "2026-10-01T00:00:00Z",
    }


def test_fixed_clock_is_normalized_and_does_not_follow_wall_clock(store, clock):
    service = Copilot(
        store,
        FixedExtractor(value=None, date="last month"),
        interpretation_time="2026-10-01T01:30:00+05:30",
        wall_clock=clock,
    )
    clock.advance(days=100)
    result = asyncio.run(service.plan("atlas", INSTRUCTION))["plan"]
    assert result["interpretation_time"] == "2026-09-30T20:00:00Z"
    assert result["filter"]["date"]["gte"] == "2026-08-01T00:00:00Z"


@pytest.mark.parametrize("owner", ["Asha Verma", "Priya"])
@pytest.mark.parametrize(
    "phrase,lower,upper",
    [
        ("last month", "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"),
        ("yesterday", "2026-09-29T00:00:00Z", "2026-09-30T00:00:00Z"),
    ],
)
def test_rollover_during_inference_and_clarification_keeps_original_clock(
    store, clock, owner, phrase, lower, upper
):
    clock.value = parse_time("2026-09-30T23:59:59Z")

    class AdvancingExtractor(FixedExtractor):
        async def extract(self, instruction):
            clock.advance(seconds=2)
            return await super().extract(instruction)

    service = Copilot(
        store,
        AdvancingExtractor(owner=owner, value=None, date=phrase),
        wall_clock=clock,
    )
    result = asyncio.run(
        service.plan(
            "atlas", INSTRUCTION.replace("Asha Verma", owner).replace("last month", phrase)
        )
    )
    if owner == "Priya":
        assert result["outcome"] == "clarification"
        clock.advance(seconds=2)
        result = service.clarify("atlas", result["operation_id"], {"owner": "priya-sharma"})
    plan = result["plan"]
    assert plan["interpretation_time"] == "2026-09-30T23:59:59Z"
    assert plan["filter"]["date"] == {
        "field": "created_at",
        "gte": lower,
        "lt": upper,
    }


@pytest.mark.parametrize("field", ["created", "updated", "entered"])
@pytest.mark.parametrize(
    "phrase,clock,timestamps",
    [
        (
            "last month",
            INTERPRETATION_TIME,
            [
                "2026-07-31T23:59:59Z",
                "2026-08-01T00:00:00Z",
                "2026-08-31T23:59:59Z",
                "2026-09-01T00:00:00Z",
            ],
        ),
        (
            "yesterday",
            "2024-03-01T12:30:00Z",
            [
                "2024-02-28T23:59:59Z",
                "2024-02-29T00:00:00Z",
                "2024-02-29T23:59:59Z",
                "2024-03-01T00:00:00Z",
            ],
        ),
    ],
)
def test_sql_date_bounds_include_start_and_exclude_end(store, field, phrase, clock, timestamps):
    with store.transaction() as conn:
        for index, timestamp in enumerate(timestamps):
            conn.execute(
                "INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "atlas",
                    f"boundary-{index}",
                    "Date boundary",
                    100,
                    "open",
                    "asha-verma",
                    "contacted",
                    timestamp,
                    timestamp,
                    timestamp,
                    1,
                ),
            )
    date, question, _ = resolve_date(phrase, f"{field} {phrase}", parse_time(clock))
    assert question is None
    actual = {
        row["id"]
        for row in store.matching("atlas", Filter(stage_id="contacted", date=date))
        if row["id"].startswith("boundary-")
    }
    assert actual == {"boundary-1", "boundary-2"}
