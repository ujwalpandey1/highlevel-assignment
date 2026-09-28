import asyncio
from datetime import timedelta

import pytest

from copilot.clock import INTERPRETATION_TIME, parse_time
from copilot.llm import Usage
from copilot.schema import Intent
from copilot.seed import seed
from copilot.service import Copilot
from copilot.store import Store


class FixedExtractor:
    def __init__(self, **fields):
        self.intent = Intent(
            decision="move",
            source_stage="Qualified",
            owner="Asha Verma",
            status="open",
            target_stage="Proposal Sent",
            value="under INR 25000",
            date=None,
            unsupported=None,
        ).model_copy(update=fields)
        self.calls = 0

    async def extract(self, instruction):
        self.calls += 1
        return self.intent, Usage()


class MutableClock:
    def __init__(self):
        self.value = parse_time(INTERPRETATION_TIME)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


@pytest.fixture(scope="session")
def seeded():
    store = Store()
    seed(store)
    yield store
    store.close()


@pytest.fixture
def store(seeded):
    result = seeded.clone()
    yield result
    result.close()


@pytest.fixture
def clock():
    return MutableClock()


@pytest.fixture
def service(store, clock):
    return Copilot(store, FixedExtractor(), wall_clock=clock)


def preview(service):
    return asyncio.run(
        service.plan(
            "atlas",
            "Move open deals owned by Asha Verma from Qualified to Proposal Sent worth under INR 25000.",
        )
    )


def confirm(service, p, **kwargs):
    return service.confirm("atlas", p["plan_id"], p["confirmation_token"], p["plan_hash"], **kwargs)
