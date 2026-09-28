"""A deliberately small CLI; every mutation requires explicit preview credentials."""

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from .clock import INTERPRETATION_TIME
from .errors import CopilotError
from .llm import HOSTED_MODEL, LOCAL_MODEL, Extractor, ModelConfig
from .schema import Filter, Intent
from .seed import seed
from .service import Copilot
from .store import Store


def money(minor: int, currency: str = "INR") -> str:
    whole, fraction = divmod(minor, 100)
    return f"{currency} {whole:,}.{fraction:02d}"


def safe(text: str) -> str:
    # Escape control sequences, bidi controls, quotes and newlines in untrusted names.
    return json.dumps(text, ensure_ascii=True)


def render(result: dict, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(result, indent=2, ensure_ascii=True)
    outcome = result.get("outcome")
    if outcome == "preview":
        lines = [
            "PREVIEW - no records moved",
            f"Workspace: {result['workspace_id']}",
            *(
                [f"Date reference: {result['plan']['interpretation_time']} (UTC)"]
                if result["plan"]["filter"]["date"]
                else []
            ),
            *["  " + line for line in result["readable"]["filters"]],
            "Target stage: " + safe(result["readable"]["target"]),
            f"Matches: {result['match_count']:,} | Would move: {result['move_count']:,} | "
            f"Total: {money(result['total_value_minor'])}",
            f"Risk: {result['risk']} | Expires: {result['expires_at']}",
            "Sample records (user-entered data):",
        ]
        for sample in result["samples"]:
            lines.append(
                f"  {sample['id']} | {safe(sample['name'])} | "
                f"{money(sample['value_minor'])} | {sample['stage_id']}"
            )
        for assumption in result["assumptions"]:
            lines.append("Assumption: " + assumption)
        lines.extend(
            [
                result["message"],
                f"Operation: {result['operation_id']}",
                f"Plan: {result['plan_id']}",
                f"Hash: {result['plan_hash']}",
            ]
        )
        if "confirmation_token" in result:
            lines.append(f"Confirmation token: {result['confirmation_token']}")
        return "\n".join(lines)
    if outcome == "clarification":
        lines = ["CLARIFICATION - no records moved", f"Operation: {result['operation_id']}"]
        for question in result["questions"]:
            lines.append(question["question"] + f" [slot: {question['slot']}]")
            for choice in question["choices"]:
                lines.append(f"  {choice['id']}: {safe(choice['label'])}")
        return "\n".join(lines + [result["message"]])
    if outcome == "executed":
        return (
            f"EXECUTED | Job: {result['job_id']} | Moved: {result['moved_count']:,}\n"
            f"Completed: {result['completed_at']} | Idempotent replay: {result['idempotent_replay']}"
        )
    if outcome == "second_confirmation_required":
        return (
            "SECOND CONFIRMATION REQUIRED - no records moved\n"
            f"Type exactly: {result['acknowledgement']}\n"
            f"Challenge token: {result['challenge_token']}\n{result['message']}"
        )
    if outcome in ("refused", "unavailable", "error"):
        return f"{outcome.upper()} [{result['code']}]\n{result['message']}"
    return json.dumps(result, indent=2, ensure_ascii=True)


def add_model_options(parser: argparse.ArgumentParser):
    parser.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    parser.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    parser.add_argument(
        "--model", help="Defaults to the documented model for the selected provider"
    )
    parser.add_argument("--recordings", type=Path, default=Path("evals/recordings"))
    parser.add_argument("--take", type=int, default=0)
    parser.add_argument(
        "--timeout", type=float, default=25, help="Seconds per call; at most two attempts"
    )


def model_config(args) -> ModelConfig:
    timeout = getattr(args, "timeout", 25)
    if not 0.05 <= timeout <= 120:
        raise CopilotError("invalid_timeout", "Timeout must be between 0.05 and 120 seconds.")
    provider = getattr(args, "provider", "ollama")
    return ModelConfig(
        mode=getattr(args, "mode", "replay"),
        provider=provider,
        model=getattr(args, "model", None)
        or (HOSTED_MODEL if provider == "openai" else LOCAL_MODEL),
        cassette_dir=getattr(args, "recordings", Path("evals/recordings")),
        take=getattr(args, "take", 0),
        call_timeout=timeout,
        total_timeout=2 * timeout + 2,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Pipeline Copilot: preview, then explicitly confirm."
    )
    root.add_argument("--db", type=Path, default=Path("data/copilot.sqlite3"))
    root.add_argument("--workspace", help="Required for every tenant-scoped command")
    root.add_argument(
        "--json", action="store_true", help="Machine-readable output, including capabilities"
    )
    sub = root.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="Create deterministic assignment data")
    seed_cmd.add_argument(
        "--reset", action="store_true", help="Explicitly replace this local database's data"
    )
    sub.add_parser("catalog", help="List this workspace's stages and owners")
    sub.add_parser("stats", help="Count and sum a supported structured filter")
    sub.choices["stats"].add_argument("--filter", default="{}", help="Strict Filter JSON")
    plan_cmd = sub.add_parser("plan", help="Interpret and preview, never execute")
    plan_cmd.add_argument("instruction")
    plan_cmd.add_argument(
        "--replace", help="Regenerate this operation and invalidate its previous preview"
    )
    plan_cmd.add_argument(
        "--clock", help="Fix the interpretation time to an ISO timestamp; default: current UTC"
    )
    add_model_options(plan_cmd)
    clarify = sub.add_parser("clarify", help="Answer all questions in one clarification round")
    clarify.add_argument("--operation", required=True)
    clarify.add_argument("--answer", action="append", required=True, metavar="SLOT=ID")
    confirm = sub.add_parser("confirm", help="Explicitly authorize the exact preview")
    confirm.add_argument("--plan", required=True)
    confirm.add_argument("--token", required=True)
    confirm.add_argument("--hash", required=True)
    confirm.add_argument("--challenge")
    confirm.add_argument("--ack")
    show = sub.add_parser("show", help="Show the immutable preview and its current state")
    show.add_argument("--plan", required=True)
    sub.add_parser("audit", help="List the most recent tenant-scoped audit events")
    sub.add_parser("schema", help="Show the strict model and bulk filter schemas")
    evaluation = sub.add_parser("eval", help="Run the semantic and execution evaluation suite")
    add_model_options(evaluation)
    evaluation.add_argument("--cases", type=Path, default=Path("evals/cases.jsonl"))
    evaluation.add_argument(
        "--split", help="Run only a named corpus split, e.g. regression or challenge"
    )
    evaluation.add_argument("--runs", type=int, default=3)
    evaluation.add_argument("--limit", type=int)
    evaluation.add_argument("--output", type=Path, default=Path("artifacts/eval-replay.json"))
    evaluation.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted record run from existing cassettes",
    )
    demo = sub.add_parser(
        "demo", help="Run the four-part demonstration on an isolated copy of seeded data"
    )
    demo.add_argument(
        "--pause", type=float, default=0, help="Reading time between scenes, 0-20 seconds"
    )
    demo.add_argument(
        "--screen", action="store_true", help="Clear the terminal between recording scenes"
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "schema":
            result = {"intent": Intent.model_json_schema(), "filter": Filter.model_json_schema()}
        elif args.command == "eval":
            from .evaluation import evaluate

            if not 1 <= args.runs <= 10:
                raise CopilotError("invalid_runs", "Choose 1 to 10 evaluation runs.")
            result = asyncio.run(evaluate(args))
        elif args.command == "demo":
            from .demo import run_demo

            if not 0 <= args.pause <= 20:
                raise CopilotError("invalid_pause", "Demo pause must be between 0 and 20 seconds.")
            return asyncio.run(run_demo(args.pause, args.screen))
        else:
            store = Store(args.db)
            if args.command == "seed":
                result = {"seed": seed(store, reset=args.reset), "clock": INTERPRETATION_TIME}
            else:
                if not args.workspace:
                    raise CopilotError(
                        "workspace_required", "Supply --workspace before the command."
                    )
                store.workspace(args.workspace)
                copilot = Copilot(
                    store,
                    Extractor(model_config(args)),
                    interpretation_time=getattr(args, "clock", None),
                )
                if args.command == "catalog":
                    result = store.catalog(args.workspace)
                elif args.command == "stats":
                    filters = Filter.model_validate_json(args.filter)
                    result = {
                        "count": store.count(args.workspace, filters),
                        "total_value_minor": store.sum(args.workspace, filters),
                        "currency": "INR",
                    }
                elif args.command == "plan":
                    result = asyncio.run(
                        copilot.plan(args.workspace, args.instruction, args.replace)
                    )
                elif args.command == "clarify":
                    answers = {}
                    for answer in args.answer:
                        if "=" not in answer:
                            raise CopilotError("invalid_answers", "Use --answer SLOT=ID.")
                        key, value = answer.split("=", 1)
                        if key in answers:
                            raise CopilotError("invalid_answers", "Each slot may be answered once.")
                        answers[key] = value
                    result = copilot.clarify(args.workspace, args.operation, answers)
                elif args.command == "confirm":
                    result = copilot.confirm(
                        args.workspace, args.plan, args.token, args.hash, args.challenge, args.ack
                    )
                elif args.command == "show":
                    result = copilot.show(args.workspace, args.plan)
                elif args.command == "audit":
                    result = {
                        "events": [
                            dict(row)
                            for row in store.connection.execute(
                                "SELECT * FROM audit WHERE workspace_id=? ORDER BY sequence DESC LIMIT 50",
                                (args.workspace,),
                            )
                        ]
                    }
                else:
                    raise AssertionError("unhandled command")
        print(render(result, args.json))
        if result.get("unsafe_actions", 0):
            return 1
        return {"error": 2, "refused": 3, "unavailable": 4}.get(result.get("outcome"), 0)
    except CopilotError as error:
        print(render(error.as_dict(), args.json))
        return 2
    except (ValueError, sqlite3.Error) as error:
        print(
            render(
                {"outcome": "error", "code": "invalid_input_or_store", "message": str(error)},
                args.json,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        if store:
            store.close()
