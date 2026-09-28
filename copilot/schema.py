"""The model never emits SQL, tenant IDs, entity IDs, timestamps, or authority."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["open", "won", "lost", "abandoned"]
DateField = Literal["created_at", "updated_at", "stage_entered_at"]
Mention = Annotated[str, Field(min_length=1, max_length=240)]
EntityId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
MinorUnits = Annotated[int, Field(ge=0, le=1_000_000_000_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Intent(StrictModel):
    # All fields are required, even when null, for provider strict JSON schemas.
    decision: Literal["move", "refuse", "clarify"]
    source_stage: Mention | None
    owner: Mention | None
    status: Mention | None
    target_stage: Mention | None
    value: Mention | None
    date: Mention | None
    unsupported: Mention | None


class DateRange(StrictModel):
    field: DateField
    gte: str | None = None
    lt: str | None = None

    @model_validator(mode="after")
    def valid_range(self):
        for value in (self.gte, self.lt):
            if value is not None:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.utcoffset() is None or not value.endswith("Z"):
                    raise ValueError("Use canonical UTC timestamps ending in Z")
                if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
                    raise ValueError("Use second-resolution ISO timestamps")
        if self.gte is None and self.lt is None:
            raise ValueError("A date range needs a bound")
        if self.gte is not None and self.lt is not None and self.gte >= self.lt:
            raise ValueError("Empty or reversed date range")
        return self


class Filter(StrictModel):
    stage_id: EntityId | None = None
    owner_id: EntityId | None = None
    status: Status | None = None
    value_min: MinorUnits | None = None
    value_max: MinorUnits | None = None
    date: DateRange | None = None

    @model_validator(mode="after")
    def valid_money_range(self):
        if (
            self.value_min is not None
            and self.value_max is not None
            and self.value_min > self.value_max
        ):
            raise ValueError("Empty or reversed monetary range")
        return self


class Plan(StrictModel):
    workspace_id: EntityId
    filter: Filter
    target_stage_id: EntityId
    interpretation_time: str
    policy_version: Literal["2026-09-28.v1"] = "2026-09-28.v1"


def canonical(value: BaseModel | dict | list) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: BaseModel | dict | list) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
