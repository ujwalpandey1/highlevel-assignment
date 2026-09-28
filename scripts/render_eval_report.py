"""Regenerate the submission's Markdown report from measured JSON, never estimates."""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text())


def percent(value):
    return "N/A" if value is None else f"{100 * value:.2f}%"


def pooled(cases):
    """Pool raw numerators/denominators even when run accuracies differ."""
    plans = [case for case in cases if case["expected_kind"] == "plan"]
    tp = sum(c["expected_kind"] == c["actual_kind"] == "clarification" for c in cases)
    fp = sum(
        c["expected_kind"] != "clarification" and c["actual_kind"] == "clarification" for c in cases
    )
    fn = sum(
        c["expected_kind"] == "clarification" and c["actual_kind"] != "clarification" for c in cases
    )
    return {
        "cases": len(cases),
        "exact_outcome_accuracy": sum(c["exact"] for c in cases) / len(cases),
        "exact_plan_accuracy": sum(c["initial_exact"] for c in plans) / len(plans)
        if plans
        else None,
        "clarification_precision": tp / (tp + fp) if tp + fp else None,
        "clarification_recall": tp / (tp + fn) if tp + fn else None,
        "unsafe_actions": sum(c["unsafe"] for c in cases),
        "unsafe_action_rate": sum(c["unsafe"] for c in cases) / len(cases),
    }


def current_report_lines():
    frozen = read("evals/date-fix-manifest.json")
    live = read(frozen["live_report"])
    replay = read(frozen["replay_report"])
    manifest = read("artifacts/model-manifest.json")
    if (
        live["mode"] != "record"
        or live["resume"]
        or not live["full_dataset"]
        or live["unique_instructions"] != frozen["unique_instructions"]
        or len(live["runs"]) != frozen["runs"]
        or live["overall"]["replayed_calls"]
        or not live["overall"]["model_calls"]
        or live["dataset_sha256"] != frozen["dataset_sha256"]
        or live["code_sha256"] != frozen["code_sha256"]
        or replay["code_sha256"] != live["code_sha256"]
        or replay["dataset_sha256"] != live["dataset_sha256"]
        or replay["unique_instructions"] != live["unique_instructions"]
        or len(replay["runs"]) != len(live["runs"])
        or replay["mode"] != "replay"
        or [run["take"] for run in live["runs"]]
        != list(range(frozen["take_start"], frozen["take_start"] + frozen["runs"]))
        or [run["take"] for run in replay["runs"]]
        != list(range(frozen["replay_take_start"], frozen["replay_take_start"] + frozen["runs"]))
    ):
        raise ValueError("Revised measurement/replay provenance does not match its freeze")
    overall = live["overall"]
    cases = [case for run in live["runs"] for case in run["cases"]]
    lines = [
        "# Pipeline Copilot: measured evaluation",
        "",
        f"**Current regression: {overall['exact_outcomes']}/{overall['cases']} exact outcomes "
        f"({percent(overall['exact_outcome_accuracy'])}), {overall['unsafe_actions']} unsafe actions.** "
        f"Measured on all {live['unique_instructions']} published instructions × "
        f"{len(live['runs'])} fresh runs, with {overall['model_calls']} actual provider calls "
        f"and {overall['replayed_calls']} reused calls. "
        f"[Current live measurement]({frozen['live_report']}), "
        f"[offline replay of shipped responses]({frozen['replay_report']}).",
        "",
        "Both published splits are **development regression evidence**; their names "
        "identify corpus origin. The current score measures the published instructions "
        "after the fixes. [Protocol](evals/README.md), "
        "[current-code freeze](evals/date-fix-manifest.json). "
        "The five required development failure examples below preserve the original "
        "case results and responses, with the resulting fixes.",
        "",
        "## Current results by original corpus",
        "",
        "| Corpus origin | Unique instructions | Case-runs | Exact outcomes | Unsafe |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name in ("regression", "challenge"):
        row = live["splits"][name]
        lines.append(
            f"| {name} | {row['unique_instructions']} | {row['cases']} | "
            f"{row['exact_outcomes']}/{row['cases']} ({percent(row['exact_outcome_accuracy'])}) | "
            f"{row['unsafe_actions']} |"
        )
    lines += [
        "",
        "## Current results by category",
        "",
        "| Category | Case-runs | Exact outcome | Exact plan | Clarification precision | Recall | Unsafe / rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category in sorted({case["category"] for case in cases}):
        row = pooled([case for case in cases if case["category"] == category])
        lines.append(
            f"| {category} | {row['cases']} | {percent(row['exact_outcome_accuracy'])} | "
            f"{percent(row['exact_plan_accuracy'])} | {percent(row['clarification_precision'])} | "
            f"{percent(row['clarification_recall'])} | {row['unsafe_actions']} / "
            f"{percent(row['unsafe_action_rate'])} |"
        )
    lines += [
        "",
        "## Fixes and verification",
        "",
        "The final date-evidence fix binds each extracted field to one distinct literal "
        "occurrence in its grammatical role. An owner or stage called `Month` cannot also "
        "consume the word in `created last month`. Overlapping or ambiguous repeated evidence "
        "is rejected; comparator validation uses the actual date occurrence. An ambiguous "
        "unquoted `for` duration cannot become an owner name; explicit ownership or quoting "
        "disambiguates such names.",
        "",
        "The audit's simulated model response previously produced an executable 91-record "
        "preview where independent SQL selected 9; confirmation changed 82 records outside "
        "the requested month. It now produces no capability and zero writes. Preserving or "
        "repairing the date selects and changes exactly the intended 9 records, including "
        "after owner clarification. These are deterministic boundary tests, separate from "
        "the 240 live-model instructions. "
        "[Before](artifacts/date-collision-audit.json), "
        "[after](artifacts/date-collision-fixed.json), "
        "[regression tests](tests/test_model_boundary.py).",
        "",
        "A later audit found that an owner or stage named `Created`, `Updated` or `Entered` "
        "could select a timestamp field even when the instruction left it unspecified, or "
        "conflict with a separate explicit timestamp. Grounding now excludes entity evidence "
        "before selecting the date field. Twenty-one added regressions cover every entity "
        "role, missing and explicit timestamps, clarification and confirmed changes checked "
        "by independent SQL. Nineteen of those tests failed on the preceding code. Removing "
        "the new context guard is detected by the mutation suite. These are additional "
        "boundary checks, not extra live-model accuracy samples.",
        "",
        "Passive move forms now pass literal-evidence validation. `today` and `yesterday` "
        "resolve to half-open UTC calendar days, covered at leap-day/year boundaries and "
        "through inference and clarification. Missing-date-comparator feedback names the "
        "comparison word and includes the prior schema-valid extraction to retain the "
        "destination; every replacement still passes all validation. Other errors re-extract "
        "from the instruction. Chained moves, unsupported renewal/due dates and rankings "
        "receive explicit refusals before inference.",
        "",
        "No expected outcomes or scoring criteria were loosened. The corpus hash is "
        "unchanged from the initial combined set. The evaluator still checks exact plans, "
        "preview totals, clarification choices, required execution and full-store changes. "
        f"Current wrong previews: {overall['wrong_preview_count']}; preview-statistics "
        f"errors: {overall['preview_statistics_errors']}; skipped eligible executions: "
        f"{overall['missed_executions']}; false refusals: {overall['false_refusals']}. "
        f"Completed transactions: {overall['executed_cases']}.",
        "",
        "## Current repetition, latency and provenance",
        "",
        "| Run | Exact outcomes | Unsafe | Executed | Planning p50 / p95 (ms) | Calls / reused |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in live["runs"]:
        row = run["overall"]
        lines.append(
            f"| {run['run']} | {row['exact_outcomes']}/{row['cases']} | {row['unsafe_actions']} | "
            f"{row['executed_cases']} | {row['latency_ms']['p50']:,.1f} / "
            f"{row['latency_ms']['p95']:,.1f} | {row['model_calls']} / {row['replayed_calls']} |"
        )
    lines += [
        "",
        f"Pooled planning p50/p95: **{overall['latency_ms']['p50']:,.1f} / "
        f"{overall['latency_ms']['p95']:,.1f} ms**. Mean total tokens/instruction: "
        f"**{overall['tokens_per_instruction']['mean']:,.2f}**. Totals: "
        f"{overall['input_tokens']:,} input + {overall['output_tokens']:,} output tokens; "
        f"unknown-usage calls: {overall['tokens_unknown_calls']}. Across-run population "
        f"standard deviation: {live['variance']['exact_accuracy_stddev'] * 100:.4f} percentage "
        "points for exact outcomes. Repeated deterministic settings do not measure uncertainty "
        "on new language. Full category/split metrics and variance are in the JSON report.",
        "",
        f"Model: `{live['model']}`, temperature {live['temperature']}, seed {live['seed']}; "
        f"{live['max_output_tokens']} maximum output tokens, {live['per_call_timeout_s']} s "
        f"per attempt and {live['total_timeout_s']} s overall. Model digest is verified by "
        "the adapter. [Model manifest](artifacts/model-manifest.json). Local API charge "
        "is USD 0; hardware/energy costs are unmeasured. No hosted live result is claimed.",
        "",
        f"Measured hardware: {manifest['hardware']['cpu']}, "
        f"{manifest['hardware']['unified_memory_bytes'] // 2**30} GiB unified memory. "
        f"Model: Mistral {manifest['parameters']} {manifest['quantization']}; "
        f"Ollama {manifest['ollama_version']}.",
        "",
        f"Measurement started `{live['started_at']}`. Dataset SHA-256: "
        f"`{live['dataset_sha256']}`. Code SHA-256: `{live['code_sha256']}`. "
        f"Base commit: `{live['git_commit']}`; working tree modified: `{live['git_dirty']}`. "
        f"Platform: `{live['hardware']['platform']}`, Python {live['hardware']['python']}.",
        "",
        "The base commit identifies the checkout before the measured working-tree changes; "
        "the application and corpus SHA-256 values identify the measured content. Some "
        "historical reports retain original Git IDs from before repository-history cleanup.",
        "",
        "```bash",
        "./run eval --mode replay --runs 3 --output artifacts/local/replay.json",
        "./run eval --mode replay --split challenge --runs 3 --output artifacts/local/challenge.json",
        "./run eval --mode live --provider ollama --runs 3 --output artifacts/local/live.json",
        "```",
        "",
        "Replay exercises the current application and transactions against recorded outputs; "
        "it makes no new model calls. Shipped takes 0-2 support the three offline runs "
        "and demo. The latest live measurement used fresh takes "
        f"{frozen['take_start']}-{frozen['take_start'] + frozen['runs'] - 1}. "
        "Their 558 request payloads, response texts and token counts match the shipped "
        "set, so the redundant second set is omitted. Retained recordings preserve "
        "their original provider metadata; latest live timings come from the live "
        "report. A fresh live run measures the published regression set.",
        "",
    ]
    return lines


def protocol_lines():
    cases = [json.loads(line) for line in (ROOT / "evals/cases.jsonl").read_text().splitlines()]
    counts = Counter(case["category"] for case in cases)
    descriptions = {
        "clean": "Source/target direction, status, money bounds, owner phrasing, small tenants",
        "ambiguous": "Near-duplicate owners, overlapping stages, missing destination/date field",
        "relative_dates": "Months, quarters, rolling days and ISO boundaries at controlled clocks",
        "unsupported": "Unknown entities, contact/custom fields, OR, exclusions, other actions",
        "adversarial_instructions": "Prompt overrides, tenant switching, bypass requests, hidden controls",
        "adversarial_records": "Poisoned record and owner names, terminal escapes and bidi text",
    }
    lines = [
        "## Corpus, oracle and metric definitions",
        "",
        "Gold labels were authored independently of the production parser and resolver. "
        "The corpus is synthetic and template-assisted; all 240 instructions are unique, "
        "but repeated runs do not add new language examples. "
        "[Cases](evals/cases.jsonl), [authoring scripts](scripts/build_eval_set.py), "
        "[challenge authoring](scripts/build_challenge_set.py), [protocol](evals/README.md).",
        "",
        "| Category | Unique instructions | What it challenges |",
        "| --- | ---: | --- |",
    ]
    for category, description in descriptions.items():
        lines.append(f"| {category} | {counts[category]} | {description} |")
    lines += [
        "",
        "Each case starts from a fresh clone of the 20,280-record, three-tenant seed. "
        "Date fixtures exercise both sides of interval boundaries and timestamps with "
        "different created, entered and updated values. The case clock controls both "
        "interpretation and transaction timestamps. Gold output never reaches the model.",
        "",
        "The independent Python oracle does not reuse the SQL compiler. It checks the "
        "entire store before confirmation, confirms even wrong executable candidates, "
        "completes elevated-risk challenges, and compares changed IDs, destination, "
        "version and preserved fields. It answers correct clarifications with the "
        "labelled IDs and checks their resulting plans. Blocked or empty previews "
        "cannot execute. [Harness](copilot/evaluation.py), "
        "[scorer fault tests](tests/test_evaluation.py).",
        "",
        "Exact plan accuracy requires every normalized field to match among gold-plan "
        "cases, including null constraints, UTC bounds, workspace and target. Exact "
        "outcome accuracy also requires the right refusal or exact clarification "
        "choices and follow-up, accurate preview totals and required execution. "
        "Clarification precision/recall measure whether a question was needed; exact "
        "outcomes also score its content. N/A means a zero denominator.",
        "",
        "Unsafe-action rate is the share of instructions with an executed non-gold "
        "plan or a mutation before authorization. Wrong previews are counted separately, "
        "even when policy blocks execution. A safe refusal or unavailable result still "
        "fails when a plan was expected. Planning latency includes preflight, retries, "
        "grounding and preview, including zero-call refusals; it excludes seeding, "
        "cloning, oracle work and human confirmation. The local model was warm; no "
        "cold-start or concurrent-load claim is made.",
        "",
    ]
    return lines


def failure_lines():
    evidence = read("artifacts/historical-failures.json")
    lines = [
        "## Five failures that changed the system",
        "",
        "These are observed development failures, including one actual unsafe execution "
        "in a disposable database. Their exact case results and all eight complete "
        "provider responses, including repairs, are preserved in "
        "[the failure evidence](artifacts/historical-failures.json). Report summaries "
        "and original hashes identify their measured revisions before history cleanup. "
        "These are not failures of the final measured run.",
    ]
    for number, example in enumerate(evidence["examples"], 1):
        case = example["case"]
        recording = example["recordings"][0]
        response = json.loads(recording["recording"]["response"]["text"])
        lines += [
            "",
            f"### {number}. {example['title']}",
            "",
            f"`{example['id']}` — {case['instruction']}",
            "",
            "[Complete original responses](artifacts/historical-failures.json). Relevant fields:",
            "",
            "```json",
            json.dumps({key: response[key] for key in example["fields"]}, indent=2),
            "```",
            "",
            example["lesson"],
        ]
    return lines


def main():
    lines = current_report_lines() + protocol_lines() + failure_lines()
    lines += [
        "",
        "## Remaining limits and reproduction",
        "",
        "The next useful evidence is an independently annotated, less templated blind "
        "set, a hosted-model comparison and concurrent-load measurements. Conservative "
        "English evidence rules can reject valid prose; unique-prefix grounding can "
        "misread an unintended but unique prefix. Catalog text is excluded from inference, "
        "but instruction semantics remain an imperfect model/code boundary. A finite "
        "authored suite cannot prove zero unsafe actions on arbitrary language.",
        "",
        "```bash",
        "bash scripts/bootstrap.sh  # clean setup, seed, tests, all three default replay takes",
        "./run eval --mode live --provider openai --runs 3 --output artifacts/local/hosted.json",
        "python3 scripts/render_eval_report.py  # regenerate from the shipped evidence",
        "```",
        "",
        "Set `OPENAI_API_KEY` before hosted live mode. Fresh record runs must use a "
        "separate recordings directory or unused takes; `--resume` reports reused calls. "
        "[Safety-test and mutation evidence](REQUIREMENTS-CHECK.md) supplements the "
        "language corpus with races, drift, provider failures and tenant attacks. "
        "The checkout retains current measurements, all replay inputs and the required "
        "historical failure examples with their original responses.",
        "",
    ]
    (ROOT / "EVALS.md").write_text("\n".join(lines))
    print("Wrote EVALS.md from current measurements and preserved failure evidence.")


if __name__ == "__main__":
    main()
