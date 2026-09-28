"""All execution authority lives here, behind an immutable server-side preview."""

import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError

from .clock import INTERPRETATION_TIME, iso, utc_now
from .errors import CopilotError, Refusal
from .grounding import Grounded, ground, preflight
from .llm import Extractor, Usage
from .schema import Intent, Plan, canonical, digest
from .store import Store

SOFT_COUNT = 500
HARD_COUNT = 5000
SOFT_VALUE = 1_000_000_000  # INR 10,000,000 in paise
HARD_VALUE = 10_000_000_000  # INR 100,000,000 in paise
PREVIEW_TTL = timedelta(minutes=10)
CHALLENGE_TTL = timedelta(minutes=2)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def failure(code: str, message: str) -> dict:
    return {"outcome": "error", "code": code, "message": message}


def risk_level(count: int, value: int, movable: int) -> str:
    if not movable:
        return "empty"
    if count > HARD_COUNT or value > HARD_VALUE:
        return "blocked"
    if count > SOFT_COUNT or value > SOFT_VALUE:
        return "elevated"
    return "normal"


def describe(plan: Plan, catalog: dict) -> dict:
    stages = {r["id"]: r["name"] for r in catalog["stages"]}
    owners = {r["id"]: r["name"] for r in catalog["owners"]}
    filters = plan.filter
    lines = []
    if filters.stage_id:
        lines.append(f"Current stage is {stages[filters.stage_id]!r}")
    if filters.owner_id:
        lines.append(f"Owner is {owners[filters.owner_id]!r}")
    if filters.status:
        lines.append(f"Status is {filters.status}")
    if filters.value_min is not None:
        lines.append(f"Value is at least INR {filters.value_min / 100:,.2f}")
    if filters.value_max is not None:
        lines.append(f"Value is at most INR {filters.value_max / 100:,.2f}")
    if filters.date:
        label = {
            "created_at": "Created",
            "updated_at": "Last updated",
            "stage_entered_at": "Entered current stage",
        }[filters.date.field]
        if filters.date.gte:
            lines.append(f"{label} on or after {filters.date.gte} (inclusive)")
        if filters.date.lt:
            lines.append(f"{label} before {filters.date.lt} (exclusive)")
    return {
        "filters": lines or ["All opportunities in this workspace"],
        "target": stages[plan.target_stage_id],
        "currency": catalog["workspace"]["currency"],
    }


class Copilot:
    def __init__(
        self,
        store: Store,
        extractor: Extractor,
        interpretation_time: str = INTERPRETATION_TIME,
        wall_clock: Callable[[], datetime] = utc_now,
    ):
        self.store = store
        self.extractor = extractor
        self.interpretation_time = interpretation_time
        self.wall_clock = wall_clock

    def _audit(self, workspace: str, event: str, object_id: str, details: dict):
        # Never log confirmation capabilities, API keys, or raw model bodies.
        self.store.connection.execute(
            "INSERT INTO audit(workspace_id,event,object_id,at,details) VALUES (?,?,?,?,?)",
            (workspace, event, object_id, iso(self.wall_clock()), canonical(details)),
        )

    def _start(self, workspace: str, instruction: str, operation_id: str | None) -> tuple[str, int]:
        with self.store.transaction() as conn:
            catalog = self.store.catalog(workspace)
            expires = iso(self.wall_clock() + PREVIEW_TTL)
            if operation_id:
                old = conn.execute(
                    "SELECT * FROM operations WHERE workspace_id=? AND id=?",
                    (workspace, operation_id),
                ).fetchone()
                if old is None:
                    raise CopilotError(
                        "operation_not_found", "Operation not found in this workspace."
                    )
                revision = old["revision"] + 1
                # Invalidate before awaiting the model, including failed regeneration.
                conn.execute(
                    "UPDATE plans SET state='superseded' WHERE workspace_id=? AND operation_id=? AND state='pending'",
                    (workspace, operation_id),
                )
                conn.execute(
                    "UPDATE operations SET revision=?,state='planning',instruction=?,intent_json=NULL,questions_json=NULL,expires_at=?,interpretation_time=?,catalog_hash=? WHERE workspace_id=? AND id=?",
                    (
                        revision,
                        instruction,
                        expires,
                        self.interpretation_time,
                        digest(catalog),
                        workspace,
                        operation_id,
                    ),
                )
            else:
                operation_id, revision = uuid.uuid4().hex, 1
                conn.execute(
                    "INSERT INTO operations VALUES (?,?,?,?,?,NULL,NULL,?,?,?)",
                    (
                        workspace,
                        operation_id,
                        revision,
                        "planning",
                        instruction,
                        expires,
                        self.interpretation_time,
                        digest(catalog),
                    ),
                )
            self._audit(workspace, "planning_started", operation_id, {"revision": revision})
        return operation_id, revision

    async def plan(self, workspace: str, instruction: str, operation_id: str | None = None) -> dict:
        start = time.perf_counter()
        op_id, revision = self._start(workspace, instruction, operation_id)
        usage = Usage()
        try:
            preflight(instruction)
            intent, usage = await self.extractor.extract(instruction)
            with self.store.transaction() as conn:
                operation = conn.execute(
                    "SELECT * FROM operations WHERE workspace_id=? AND id=?", (workspace, op_id)
                ).fetchone()
                if operation["revision"] != revision or operation["state"] != "planning":
                    raise CopilotError("superseded", "A newer instruction replaced this one.")
                catalog = self.store.catalog(workspace)
                if digest(catalog) != operation["catalog_hash"]:
                    raise CopilotError(
                        "catalog_changed", "The workspace catalog changed; request a fresh preview."
                    )
                grounded = ground(intent, instruction, catalog, self.interpretation_time)
                if grounded.questions:
                    conn.execute(
                        "UPDATE operations SET state='clarification',intent_json=?,questions_json=? WHERE workspace_id=? AND id=?",
                        (intent.model_dump_json(), canonical(grounded.questions), workspace, op_id),
                    )
                    result = {
                        "outcome": "clarification",
                        "operation_id": op_id,
                        "questions": grounded.questions,
                        "expires_at": operation["expires_at"],
                        "message": "Choose one listed ID for each question. No records have moved.",
                    }
                else:
                    result = self._preview(op_id, revision, grounded, catalog)
        except Refusal as error:
            result = {
                "outcome": "refused",
                "code": error.code,
                "message": error.message,
                "operation_id": op_id,
            }
        except CopilotError as error:
            result = {
                "outcome": "unavailable",
                "code": error.code,
                "message": error.message,
                "operation_id": op_id,
            }
            if hasattr(error, "usage"):
                result["usage"] = error.usage
        except ValidationError:
            result = {
                "outcome": "refused",
                "code": "invalid_resolved_plan",
                "message": "The resolved plan is invalid. Nothing was moved.",
                "operation_id": op_id,
            }
        if result["outcome"] in ("refused", "unavailable"):
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE operations SET state='failed' WHERE workspace_id=? AND id=? AND revision=?",
                    (workspace, op_id, revision),
                )
                self._audit(workspace, result["outcome"], op_id, {"code": result["code"]})
        result.setdefault("usage", usage.as_dict())
        result["usage"]["elapsed_ms"] = (time.perf_counter() - start) * 1000
        return result

    def clarify(self, workspace: str, operation_id: str, answers: dict[str, str]) -> dict:
        with self.store.transaction() as conn:
            operation = conn.execute(
                "SELECT * FROM operations WHERE workspace_id=? AND id=?", (workspace, operation_id)
            ).fetchone()
            if operation is None:
                raise CopilotError("operation_not_found", "Operation not found in this workspace.")
            if operation["state"] != "clarification":
                raise CopilotError(
                    "clarification_used",
                    "Only one clarification round is supported. Start a new instruction.",
                )
            if iso(self.wall_clock()) >= operation["expires_at"]:
                raise CopilotError("expired", "The clarification expired. Start a new instruction.")
            questions = json.loads(operation["questions_json"])
            if set(answers) != {q["slot"] for q in questions}:
                raise CopilotError(
                    "invalid_answers", "Answer every question using only the listed slots and IDs."
                )
            for question in questions:
                if answers[question["slot"]] not in {c["id"] for c in question["choices"]}:
                    raise CopilotError(
                        "invalid_answers", "An answer is not one of the offered choices."
                    )
            catalog = self.store.catalog(workspace)
            if digest(catalog) != operation["catalog_hash"]:
                raise CopilotError(
                    "catalog_changed", "The workspace catalog changed; start a new instruction."
                )
            intent = Intent.model_validate_json(operation["intent_json"])
            grounded = ground(
                intent, operation["instruction"], catalog, operation["interpretation_time"], answers
            )
            if grounded.questions:
                raise CopilotError(
                    "unresolved", "Still ambiguous after one round. Restate the instruction."
                )
            result = self._preview(operation_id, operation["revision"], grounded, catalog)
            self._audit(workspace, "clarified", operation_id, {"slots": sorted(answers)})
            return result

    def _preview(self, operation_id: str, revision: int, grounded: Grounded, catalog: dict) -> dict:
        # Caller owns the same write transaction used for selection and persistence.
        plan = grounded.plan
        assert plan is not None
        self.store.validate_entities(plan)
        summary = self.store.aggregate(plan.workspace_id, plan.filter, plan.target_stage_id)
        count, total, movable = summary["count"], summary["total"], summary["movable"]
        risk = risk_level(count, total, movable)
        # A blocked/no-op preview has no execution capability, so only its samples
        # need materializing. Executable previews fingerprint every matching row.
        rows = self.store.matching(
            plan.workspace_id, plan.filter, limit=5 if risk in ("blocked", "empty") else None
        )
        plan_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        now = self.wall_clock()
        expires = iso(now + PREVIEW_TTL)
        plan_hash = digest(plan)
        view = describe(plan, catalog)
        preview = {
            "outcome": "preview",
            "workspace_id": plan.workspace_id,
            "operation_id": operation_id,
            "revision": revision,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "plan": plan.model_dump(mode="json"),
            "readable": view,
            "match_count": count,
            "move_count": movable,
            "already_in_target": count - movable,
            "total_value_minor": total,
            "samples": rows[:5],
            "risk": risk,
            "expires_at": expires,
            "executable": risk in ("normal", "elevated"),
            "assumptions": grounded.assumptions,
            "message": {
                "blocked": "Blast-radius limit exceeded. Narrow the filter and request a new preview.",
                "empty": "No stage changes to make.",
                "elevated": "Explicit confirmation followed by a separate count-and-target acknowledgement is required.",
                "normal": "Review this exact plan, then explicitly confirm. Nothing has moved.",
            }[risk],
        }
        self.store.connection.execute(
            "INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                plan.workspace_id,
                plan_id,
                operation_id,
                revision,
                canonical(plan),
                plan_hash,
                self.store.snapshot(rows),
                digest(catalog),
                count,
                total,
                risk,
                token_hash(token),
                "pending",
                expires,
                iso(now),
                None,
                canonical(preview),
            ),
        )
        self.store.connection.execute(
            "UPDATE operations SET state='preview' WHERE workspace_id=? AND id=?",
            (plan.workspace_id, operation_id),
        )
        self._audit(
            plan.workspace_id,
            "previewed",
            plan_id,
            {"plan_hash": plan_hash, "match_count": count, "risk": risk},
        )
        if preview["executable"]:
            preview["confirmation_token"] = token
        return preview

    def confirm(
        self,
        workspace: str,
        plan_id: str,
        token: str,
        plan_hash: str,
        challenge_token: str | None = None,
        acknowledgement: str | None = None,
    ) -> dict:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE workspace_id=? AND id=?", (workspace, plan_id)
            ).fetchone()
            if row is None:
                raise CopilotError("plan_not_found", "Plan not found in this workspace.")
            if not hmac.compare_digest(row["token_hash"], token_hash(token)):
                raise CopilotError(
                    "invalid_confirmation", "The confirmation does not belong to this preview."
                )
            if not hmac.compare_digest(row["plan_hash"], plan_hash):
                raise CopilotError(
                    "plan_mismatch", "The plan fingerprint does not match the preview."
                )
            if row["state"] == "executed":
                job = conn.execute(
                    "SELECT * FROM jobs WHERE workspace_id=? AND id=?", (workspace, row["job_id"])
                ).fetchone()
                return {
                    "outcome": "executed",
                    "job_id": job["id"],
                    "moved_count": job["moved_count"],
                    "completed_at": job["completed_at"],
                    "idempotent_replay": True,
                }
            if row["state"] != "pending":
                raise CopilotError(
                    "invalidated_preview", "This preview is no longer valid. Request a fresh one."
                )
            if row["risk"] in ("blocked", "empty"):
                raise CopilotError("blast_radius", "This preview cannot authorize a move.")
            now = self.wall_clock()
            if iso(now) >= row["expires_at"]:
                conn.execute(
                    "UPDATE plans SET state='expired' WHERE workspace_id=? AND id=?",
                    (workspace, plan_id),
                )
                return failure("expired", "The preview expired. Request a fresh one.")
            operation = conn.execute(
                "SELECT revision,state FROM operations WHERE workspace_id=? AND id=?",
                (workspace, row["operation_id"]),
            ).fetchone()
            if operation["revision"] != row["revision"] or operation["state"] != "preview":
                raise CopilotError("superseded", "A regenerated plan replaced this preview.")
            plan = Plan.model_validate_json(row["plan_json"])
            if plan.workspace_id != workspace or digest(plan) != row["plan_hash"]:
                raise CopilotError("plan_integrity", "Stored plan integrity check failed.")
            self.store.validate_entities(plan)
            matches = self.store.matching(workspace, plan.filter)
            if (
                self.store.snapshot(matches) != row["snapshot_hash"]
                or digest(self.store.catalog(workspace)) != row["catalog_hash"]
            ):
                conn.execute(
                    "UPDATE plans SET state='stale' WHERE workspace_id=? AND id=?",
                    (workspace, plan_id),
                )
                self._audit(workspace, "stale_preview_rejected", plan_id, {})
                return failure(
                    "stale_preview",
                    "Records or catalog changed after preview. Nothing moved; request a fresh preview.",
                )
            actual_risk = risk_level(
                len(matches),
                sum(r["value_minor"] for r in matches),
                sum(r["stage_id"] != plan.target_stage_id for r in matches),
            )
            if actual_risk != row["risk"] or actual_risk in ("blocked", "empty"):
                raise CopilotError(
                    "blast_radius", "This plan is not executable. Narrow the filter."
                )
            if actual_risk == "elevated":
                challenge = conn.execute(
                    "SELECT * FROM challenges WHERE workspace_id=? AND plan_id=?",
                    (workspace, plan_id),
                ).fetchone()
                if challenge_token is None and acknowledgement is None:
                    second_token = secrets.token_urlsafe(32)
                    phrase = f"MOVE {row['match_count']} IN {workspace} TO {plan.target_stage_id}"
                    conn.execute(
                        "INSERT OR REPLACE INTO challenges VALUES (?,?,?,?,?)",
                        (
                            workspace,
                            plan_id,
                            token_hash(second_token),
                            phrase,
                            min(row["expires_at"], iso(now + CHALLENGE_TTL)),
                        ),
                    )
                    self._audit(workspace, "second_confirmation_requested", plan_id, {})
                    return {
                        "outcome": "second_confirmation_required",
                        "challenge_token": second_token,
                        "acknowledgement": phrase,
                        "plan_id": plan_id,
                        "message": "Review the count and target, then submit this exact phrase in a separate confirmation.",
                    }
                if (
                    challenge is None
                    or challenge_token is None
                    or acknowledgement is None
                    or iso(now) >= challenge["expires_at"]
                    or not hmac.compare_digest(challenge["token_hash"], token_hash(challenge_token))
                    or not hmac.compare_digest(challenge["phrase"], acknowledgement)
                ):
                    raise CopilotError(
                        "invalid_second_confirmation", "A valid second confirmation is required."
                    )
            job_id, moved = self._bulk_move(plan, iso(now))
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)", (workspace, job_id, plan_id, moved, iso(now))
            )
            conn.execute(
                "UPDATE plans SET state='executed',job_id=? WHERE workspace_id=? AND id=?",
                (job_id, workspace, plan_id),
            )
            conn.execute(
                "UPDATE operations SET state='executed' WHERE workspace_id=? AND id=?",
                (workspace, row["operation_id"]),
            )
            self._audit(
                workspace,
                "executed",
                plan_id,
                {"job_id": job_id, "moved_count": moved, "plan_hash": plan_hash},
            )
            return {
                "outcome": "executed",
                "job_id": job_id,
                "moved_count": moved,
                "completed_at": iso(now),
                "idempotent_replay": False,
            }

    def _bulk_move(self, plan: Plan, at: str) -> tuple[str, int]:
        """Private bulkMove(filter,targetStage), callable only after checks under the lock."""
        where, args = self.store.compile_filter(plan.workspace_id, plan.filter)
        cursor = self.store.connection.execute(
            f"UPDATE opportunities SET stage_id=?,stage_entered_at=?,updated_at=?,version=version+1 "
            f"WHERE {where} AND stage_id != ?",
            [plan.target_stage_id, at, at, *args, plan.target_stage_id],
        )
        return uuid.uuid4().hex, cursor.rowcount

    def show(self, workspace: str, plan_id: str) -> dict:
        row = self.store.connection.execute(
            "SELECT preview_json,state,job_id FROM plans WHERE workspace_id=? AND id=?",
            (workspace, plan_id),
        ).fetchone()
        if row is None:
            raise CopilotError("plan_not_found", "Plan not found in this workspace.")
        return {**json.loads(row["preview_json"]), "state": row["state"], "job_id": row["job_id"]}
