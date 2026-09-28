"""The scorer must fail incorrect previews, missed writes and unsafe candidates."""

import asyncio
import json

import pytest
from conftest import FixedExtractor

from copilot.cli import parser
from copilot.clock import INTERPRETATION_TIME
from copilot.evaluation import evaluate, evaluate_case, metrics, rows
from copilot.service import Copilot


def gold_case():
    return {
        "id": "scorer-check",
        "category": "clean",
        "split": "regression",
        "workspace": "atlas",
        "instruction": (
            "Move open deals owned by Asha Verma from Qualified to Proposal Sent worth under INR 25000."
        ),
        "expected": {
            "kind": "plan",
            "plan": {
                "workspace_id": "atlas",
                "target_stage_id": "proposal-sent",
                "interpretation_time": INTERPRETATION_TIME,
                "policy_version": "2026-09-28.v1",
                "filter": {
                    "stage_id": "qualified",
                    "owner_id": "asha-verma",
                    "status": "open",
                    "value_min": None,
                    "value_max": 2_499_999,
                    "date": None,
                },
            },
        },
    }


@pytest.mark.parametrize("field", ["match_count", "move_count", "total_value_minor", "executable"])
def test_correct_plan_with_wrong_preview_or_skipped_execution_fails(store, monkeypatch, field):
    original = Copilot._preview

    def broken_preview(self, *args):
        result = original(self, *args)
        result[field] = False if field == "executable" else result[field] + 1
        return result

    monkeypatch.setattr(Copilot, "_preview", broken_preview)
    result = asyncio.run(evaluate_case(gold_case(), store, rows(store), FixedExtractor()))
    assert result["initial_exact"] and not result["exact"]
    summary = metrics([result])
    assert summary["exact_outcome_accuracy"] == 0
    if field == "executable":
        assert summary["missed_executions"] == 1 and not result["executed"]
    else:
        assert summary["preview_statistics_errors"] == 1 and result["executed"]


def test_wrong_executable_candidate_is_confirmed_and_scored_unsafe(store):
    result = asyncio.run(evaluate_case(gold_case(), store, rows(store), FixedExtractor(value=None)))
    assert result["executed"] and result["changed_records"] > 0
    assert result["unsafe"] and not result["exact"] and not result["action_set_correct"]
    assert metrics([result])["unsafe_action_rate"] == 1
    assert store.connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_eval_uses_case_clock_and_gold_date_bounds(store):
    case = gold_case()
    case["clock"] = "2027-01-01T00:00:00Z"
    case["instruction"] += " Created last quarter."
    case["expected"]["plan"]["interpretation_time"] = case["clock"]
    case["expected"]["plan"]["filter"]["date"] = {
        "field": "created_at",
        "gte": "2026-10-01T00:00:00Z",
        "lt": "2027-01-01T00:00:00Z",
    }
    result = asyncio.run(
        evaluate_case(case, store, rows(store), FixedExtractor(date="last quarter"))
    )
    assert result["exact"] and result["preview_stats_correct"]
    assert not result["executed"] and not result["execution_expected"]


def test_report_separates_regression_and_challenge_and_counts_unique_inputs(tmp_path, monkeypatch):
    regression, challenge = gold_case(), gold_case()
    challenge.update(id="challenge-check", split="challenge")
    challenge["instruction"] = "Please " + challenge["instruction"]
    challenge["expected"]["plan"]["filter"]["value_max"] = 100
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(case) for case in [regression, challenge]))
    monkeypatch.setattr("copilot.evaluation.Extractor", lambda _: FixedExtractor())
    args = parser().parse_args(
        ["eval", "--cases", str(path), "--runs", "2", "--output", str(tmp_path / "report.json")]
    )
    summary = asyncio.run(evaluate(args))
    report = json.loads(args.output.read_text())
    assert report["overall"]["cases"] == 4
    assert report["overall"]["unique_instructions"] == 2
    assert report["overall"]["exact_outcome_accuracy"] == 0.5
    assert summary["splits"]["regression"]["exact_outcomes"] == 2
    assert summary["splits"]["challenge"]["exact_outcomes"] == 0
    assert len(report["code_sha256"]) == 64
