"""Deterministic interpretation of names, money and calendar boundaries."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from .clock import iso, parse_time
from .errors import InvalidExtraction, Refusal
from .schema import DateRange, Filter, Intent, Plan


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def name_key(text: str) -> str:
    return " ".join(re.findall(r"\w+", normalized(text)))


def preflight(instruction: str):
    if not 1 <= len(instruction.strip()) <= 2000:
        raise Refusal("instruction_size", "Use an instruction between 1 and 2000 characters.")
    if any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in instruction):
        raise Refusal("control_characters", "Remove hidden or terminal control characters.")
    lowered = normalized(instruction)
    attacks = (
        r"\bignore\b.{0,40}\b(instructions?|rules?|previous|system)\b",
        r"\b(skip|bypass|disable|without)\b.{0,30}\b(confirm\w*|preview|safety|validation)\b",
        r"\b(system prompt|developer message|api key|secrets?|execute sql|drop table)\b",
        r"\b(workspace|tenant)s?\b",
        r"<\s*/?\s*(system|instructions?)\b",
        r"\b(role|target_stage_id|workspace_id)\s*[=:]",
    )
    if any(re.search(pattern, lowered) for pattern in attacks):
        raise Refusal(
            "unsafe_instruction", "Instruction overrides and tenant switching are not supported."
        )
    if re.search(
        r"\b(?:worth|value|over|under|above|below|at least|at most|between)\s+(?:usd|eur|gbp)\b|[$€]\s*\d",
        lowered,
    ):
        raise Refusal(
            "currency_mismatch",
            "This workspace's monetary filters use INR; currency conversion is unsupported.",
        )
    unsupported = (
        r"\b(contacts?|pune|mumbai|cities|city|country|countries|email|emails|phone|notes?|tags?|"
        r"probability|forecast|custom fields?|industry|revenue|employees|region|address|score|"
        r"webhooks?|delete|export|currency conversion|assign|reassign|rename|discount|tasks?|named)\b",
        r"\b(top|bottom|largest|smallest)\s+\d+\b",
        r"\b(except|unless|excluding|not owned|not in|either)\b",
        r"\b(or)\b",
    )
    if any(re.search(pattern, lowered) for pattern in unsupported):
        raise Refusal(
            "unsupported_request",
            "Only a single bulk stage move with supported AND filters is available.",
        )
    if re.search(r"\bcreated\b", lowered) and re.search(r"\bupdated\b", lowered):
        raise Refusal("multiple_date_fields", "This API supports one timestamp field per filter.")


# Grammar words may remain after all extracted literal spans are accounted for.
# Field-specific words (open, Priya, 30, INR, quarter, etc.) are deliberately absent.
# This catches dropped constraints; it does not prove semantic equivalence.
GRAMMAR = set(
    """
please kindly could can would you i want need let us move moves moving shift transfer
advance progress send put take bump set change every all any deals deal opportunities
opportunity from to into in at on stage stages the a an that which who whose are is be
been were was has have had currently now owned owning owns own owner by of for with and as status
worth value valued amount amounts between total their these those matching sitting stuck
remaining stayed stays stay more than less over under above below at least most exactly
created updated entered last entry date dates since before after during aged older days
day months month weeks week years year ago this using only s belonging belongs pipeline current
""".split()
)


def validate_evidence(intent: Intent, instruction: str):
    text = normalized(instruction)
    # A clarification can later become executable without another model call.
    # It must preserve the same literal constraints as an immediate move.
    if intent.decision == "refuse":
        return
    if intent.target_stage is None and re.search(r"\b(?:to|into)\s+\w", text):
        raise InvalidExtraction("target_stage is missing: copy the destination after to/into")
    if intent.owner is None and re.search(
        r"\bowned\s+by\b|\b\w+['’]s\s+(?:(?:open|won|lost|abandoned)\s+)?(?:deals?|opportunities)\b|\bowns\b",
        text,
    ):
        raise InvalidExtraction(
            "owner is missing: copy the full name before possessive 's or after owned by; do not omit the owner"
        )
    explicit_status = re.findall(
        r"\b(open|won|lost|abandoned)\s+(?:deals?|opportunities)\b|"
        r"\bstatus\s+(?:is\s+)?(open|won|lost|abandoned)\b",
        text,
    )
    status_values = {left or right for left, right in explicit_status}
    if status_values and (
        len(status_values) != 1 or normalized(intent.status or "") not in status_values
    ):
        raise InvalidExtraction(
            "status is missing or wrong: status adjectives before deals are NOT source stages"
        )
    if intent.source_stage:
        source = re.escape(normalized(intent.source_stage))
        if not re.search(
            r"\b(?:from|in|at)\s+(?:the\s+)?(?:stage\s+)?[\"']?" + source + r"(?!\w)", text
        ):
            raise InvalidExtraction(
                "source_stage needs an explicit from/in/at clause; do not infer it from status"
            )
    if intent.target_stage:
        target = re.escape(normalized(intent.target_stage))
        if not re.search(
            r"\b(?:to|into)\s+(?:the\s+)?(?:stage\s+)?[\"']?" + target + r"(?!\w)", text
        ):
            raise InvalidExtraction("target_stage needs an explicit to/into destination clause")
    if intent.status and not status_values:
        status_quote = re.escape(normalized(intent.status))
        if not re.search(
            r"(?<!\w)"
            + status_quote
            + r"\s+(?:deals?|opportunities)\b|\bstatus\s+(?:is\s+)?"
            + status_quote
            + r"(?!\w)",
            text,
        ):
            raise InvalidExtraction(
                "status needs a status adjective before deals or an explicit status clause"
            )
    if intent.date:
        phrase = re.escape(normalized(intent.date))
        if re.search(
            r"\b(?:before|after|since|on|older than|more than)\s+(?:the\s+)?" + phrase + r"(?!\w)",
            text,
        ):
            raise InvalidExtraction(
                "date dropped its comparator: include before/after/since/on exactly as written"
            )
    spans: list[tuple[int, int]] = []
    for field in ("target_stage", "source_stage", "owner", "status", "value", "date"):
        value = getattr(intent, field)
        if value is None:
            continue
        quote = normalized(value)
        matches = list(re.finditer(r"(?<!\w)" + re.escape(quote) + r"(?!\w)", text))
        if not matches:
            raise InvalidExtraction(
                f"{field} must be copied verbatim, including its exact comparator; never rewrite it"
            )
        spans.extend((match.start(), match.end()) for match in matches)
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    residual = set(re.findall(r"[\w$€₹%]+", "".join(chars))) - GRAMMAR
    if residual:
        raise InvalidExtraction(
            "Unaccounted instruction terms; preserve every constraint or refuse"
        )
    if intent.unsupported is not None:
        raise InvalidExtraction("A move cannot also contain an unsupported constraint")
    if intent.source_stage and intent.target_stage:
        # Protect the common explicit from/to direction independently of the model.
        source = re.escape(normalized(intent.source_stage))
        target = re.escape(normalized(intent.target_stage))
        if re.search(r"\bfrom\s+[\"']?" + target + r"\b", text) and re.search(
            r"\b(?:to|into)\s+[\"']?" + source + r"\b", text
        ):
            raise InvalidExtraction("Source and target direction is reversed")


@dataclass(frozen=True)
class Grounded:
    plan: Plan | None
    questions: list[dict]
    assumptions: list[str]


def resolve_name(
    mention: str | None, entries: list[dict], slot: str
) -> tuple[str | None, dict | None]:
    if mention is None:
        if slot != "target_stage":
            return None, None
        return None, {
            "slot": slot,
            "mention": None,
            "question": "Which target stage?",
            "choices": [{"id": r["id"], "label": r["name"]} for r in entries],
        }
    needle = name_key(mention)
    exact = [r for r in entries if name_key(r["name"]) == needle]
    if len(exact) == 1:
        return exact[0]["id"], None
    candidates = exact or [
        r
        for r in entries
        if name_key(r["name"]).startswith(needle)
        or (
            " " not in needle
            and any(word.startswith(needle) for word in name_key(r["name"]).split())
        )
    ]
    if len(candidates) == 1 and len(needle) >= 4:
        return candidates[0]["id"], None
    if not candidates:
        raise Refusal(
            "unknown_entity", f"No {slot.replace('_', ' ')} matches that name in this workspace."
        )
    return None, {
        "slot": slot,
        "mention": mention,
        "question": f"Which {slot.replace('_', ' ')} did you mean?",
        "choices": [{"id": r["id"], "label": r["name"]} for r in candidates],
    }


AMOUNT = r"(?:INR\s*|₹\s*)?([\d,]+(?:\.\d{1,2})?)\s*(k|m|lakhs?|crores?)?"


def minor_amount(number: str, unit: str | None) -> int:
    if "," in number and not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", number):
        raise Refusal("money_format", "Use ungrouped numbers or three-digit comma groups.")
    factors = {
        None: 1,
        "k": 1000,
        "m": 1_000_000,
        "lakh": 100_000,
        "lakhs": 100_000,
        "crore": 10_000_000,
        "crores": 10_000_000,
    }
    try:
        amount = Decimal(number.replace(",", "")) * factors[unit] * 100
    except (InvalidOperation, KeyError) as error:
        raise Refusal(
            "money_format", "The monetary value could not be resolved exactly."
        ) from error
    if amount != amount.to_integral_value() or not 0 <= amount <= 1_000_000_000_000:
        raise Refusal("money_range", "Use a nonnegative value with at most two decimal places.")
    return int(amount)


def resolve_money(phrase: str | None, currency: str) -> tuple[int | None, int | None]:
    if phrase is None:
        return None, None
    text = normalized(phrase).removeprefix("worth ").removeprefix("value ").strip()
    if currency != "INR" or re.search(r"[$€]|\b(usd|eur|gbp)\b", text):
        raise Refusal("currency_mismatch", "Values must use this workspace's INR currency.")
    pattern = AMOUNT.lower()
    match = re.fullmatch(r"between\s+" + pattern + r"\s+and\s+" + pattern, text)
    if match:
        low, high = minor_amount(match[1], match[2]), minor_amount(match[3], match[4])
        if low > high:
            raise Refusal("reversed_range", "The minimum value is greater than the maximum.")
        return low, high
    match = re.fullmatch(
        r"(at least|at most|over|above|more than|under|below|less than|exactly|>=|<=|>|<|=)\s*"
        + pattern,
        text,
    )
    if not match:
        raise Refusal(
            "money_format",
            "Use over, under, at least, at most, exactly, or between for value filters.",
        )
    operator, number, unit = match.groups()
    value = minor_amount(number, unit)
    if operator in ("over", "above", "more than", ">"):
        return value + 1, None
    if operator in ("under", "below", "less than", "<"):
        if value == 0:
            raise Refusal("empty_range", "No nonnegative deal value is below zero.")
        return None, value - 1
    if operator in ("at least", ">="):
        return value, None
    if operator in ("at most", "<="):
        return None, value
    return value, value


def month_start(clock: datetime, delta: int = 0) -> datetime:
    month = clock.year * 12 + clock.month - 1 + delta
    year, month = divmod(month, 12)
    return clock.replace(
        year=year, month=month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def resolve_date(
    phrase: str | None, instruction: str, clock: datetime, override: str | None = None
) -> tuple[DateRange | None, dict | None, list[str]]:
    if phrase is None:
        return None, None, []
    text = normalized(phrase)
    context = normalized(instruction)
    fields = set()
    if re.search(r"\bcreat(?:ed|ion)\b", context):
        fields.add("created_at")
    if re.search(r"\bupdat(?:ed|e)\b", context):
        fields.add("updated_at")
    if re.search(r"\b(entered|entry|stuck|sitting|stayed|stage-entered)\b|\bbeen in\b", context):
        fields.add("stage_entered_at")
    if len(fields) > 1:
        raise Refusal("multiple_date_fields", "This API supports one timestamp field per filter.")
    if override:
        field = override
    elif len(fields) == 1:
        field = fields.pop()
    else:
        return (
            None,
            {
                "slot": "date_field",
                "mention": phrase,
                "question": "Which timestamp should the date filter use?",
                "choices": [
                    {"id": key, "label": label}
                    for key, label in (
                        ("created_at", "Created"),
                        ("updated_at", "Last updated"),
                        ("stage_entered_at", "Entered current stage"),
                    )
                ],
            },
            [],
        )
    text = re.sub(
        r"^(?:last updated|created|updated|entered (?:the )?(?:current )?stage|entered|"
        r"stage-entered|stuck|sitting|been in (?:the )?stage)\s*",
        "",
        text,
    )
    text = re.sub(r"^(?:for|during|in)\s+", "", text)
    text = re.sub(r"^the\s+", "", text)
    lower = upper = None
    assumptions: list[str] = []
    if text == "last month":
        lower, upper = month_start(clock, -1), month_start(clock)
    elif text == "this month":
        lower, upper = month_start(clock), clock
    elif text in ("last quarter", "this quarter"):
        start = month_start(clock, -((clock.month - 1) % 3))
        lower, upper = (month_start(start, -3), start) if text == "last quarter" else (start, clock)
    elif match := re.fullmatch(r"(?:last|past) (\d{1,4}) days?", text):
        lower, upper = clock - timedelta(days=int(match[1])), clock
    elif match := re.fullmatch(r"(?:over|more than|older than) (\d{1,4}) days?", text):
        upper = clock - timedelta(days=int(match[1]))
    elif text in ("over a month", "more than a month", "over one month"):
        upper = clock - timedelta(days=30)
        assumptions.append(
            "Duration 'a month' means 30 elapsed days; calendar 'last month' is different."
        )
    elif match := re.fullmatch(r"(?:before|after|since|on) (\d{4}-\d{2}-\d{2})", text):
        try:
            day = parse_time(match[1] + "T00:00:00Z")
        except ValueError as error:
            raise Refusal("invalid_date", "That calendar date does not exist.") from error
        if text.startswith("before"):
            upper = day
        elif text.startswith("after"):
            lower = day + timedelta(days=1)
        elif text.startswith("since"):
            lower = day
        else:
            lower, upper = day, day + timedelta(days=1)
    elif match := re.fullmatch(r"between (\d{4}-\d{2}-\d{2}) and (\d{4}-\d{2}-\d{2})", text):
        try:
            lower = parse_time(match[1] + "T00:00:00Z")
            upper = parse_time(match[2] + "T00:00:00Z") + timedelta(days=1)
        except ValueError as error:
            raise Refusal("invalid_date", "That calendar date does not exist.") from error
    elif match := re.fullmatch(r"(before |after |in )?q([1-4])(?: (\d{4}))?", text):
        year = int(match[3]) if match[3] else clock.year
        if not 1900 <= year <= 2200:
            raise Refusal("date_range", "Use a year between 1900 and 2200.")
        if not match[3]:
            assumptions.append(f"Quarter without a year uses the controlled clock's year: {year}.")
        start = clock.replace(
            year=year,
            month=1 + 3 * (int(match[2]) - 1),
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if match[1] == "before ":
            upper = start
        elif match[1] == "after ":
            lower = month_start(start, 3)
        else:
            lower, upper = start, month_start(start, 3)
    else:
        raise Refusal(
            "date_format", "Use calendar month/quarter, rolling days, or an ISO date range."
        )
    if lower is not None and upper is not None and lower >= upper:
        raise Refusal("empty_date_range", "The date range is empty or reversed.")
    return (
        DateRange(field=field, gte=iso(lower) if lower else None, lt=iso(upper) if upper else None),
        None,
        assumptions,
    )


def ground(
    intent: Intent,
    instruction: str,
    catalog: dict,
    interpretation_time: str,
    answers: dict[str, str] | None = None,
) -> Grounded:
    answers = answers or {}
    if intent.decision == "refuse" or intent.unsupported:
        raise Refusal(
            "unsupported_request",
            "The complete instruction cannot be expressed by supported filters.",
        )
    questions, resolved, assumptions = [], {}, []
    for slot, entries in (
        ("source_stage", catalog["stages"]),
        ("target_stage", catalog["stages"]),
        ("owner", catalog["owners"]),
    ):
        entity, question = resolve_name(getattr(intent, slot), entries, slot)
        if slot in answers:
            entity, question = answers[slot], None
        if question:
            questions.append(question)
        resolved[slot] = entity
    status = normalized(intent.status) if intent.status else None
    if status and status not in catalog["workspace"]["statuses"]:
        raise Refusal("unknown_status", "Supported statuses are open, won, lost and abandoned.")
    low, high = resolve_money(intent.value, catalog["workspace"]["currency"])
    date, question, date_assumptions = resolve_date(
        intent.date, instruction, parse_time(interpretation_time), answers.get("date_field")
    )
    assumptions.extend(date_assumptions)
    if intent.value and not re.search(r"inr|₹", intent.value, re.I):
        assumptions.append("Unqualified monetary amounts are in the workspace currency, INR.")
    if question:
        questions.append(question)
    if questions:
        return Grounded(None, questions, assumptions)
    if intent.decision == "clarify" and not answers:
        raise Refusal(
            "unresolved_instruction",
            "Please restate one move with an explicit target and supported filters.",
        )
    return Grounded(
        Plan(
            workspace_id=catalog["workspace"]["id"],
            interpretation_time=interpretation_time,
            target_stage_id=resolved["target_stage"],
            filter=Filter(
                stage_id=resolved["source_stage"],
                owner_id=resolved["owner"],
                status=status,
                value_min=low,
                value_max=high,
                date=date,
            ),
        ),
        [],
        assumptions,
    )
