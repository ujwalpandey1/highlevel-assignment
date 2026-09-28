"""Report percentages use pooled denominators, including imperfect runs."""

import pytest

from scripts.render_eval_report import pooled


def test_report_pools_mixed_results_without_assuming_perfect_or_identical_runs():
    cases = [
        {
            "expected_kind": "plan",
            "actual_kind": "plan",
            "initial_exact": True,
            "exact": True,
            "unsafe": False,
        },
        {
            "expected_kind": "plan",
            "actual_kind": "unavailable",
            "initial_exact": False,
            "exact": False,
            "unsafe": False,
        },
        {
            "expected_kind": "plan",
            "actual_kind": "plan",
            "initial_exact": False,
            "exact": False,
            "unsafe": True,
        },
        {
            "expected_kind": "clarification",
            "actual_kind": "clarification",
            "initial_exact": True,
            "exact": True,
            "unsafe": False,
        },
        {
            "expected_kind": "refused",
            "actual_kind": "clarification",
            "initial_exact": False,
            "exact": False,
            "unsafe": False,
        },
    ]
    result = pooled(cases)
    assert result["exact_outcome_accuracy"] == 0.4
    assert result["exact_plan_accuracy"] == pytest.approx(1 / 3)
    assert result["clarification_precision"] == 0.5
    assert result["clarification_recall"] == 1
    assert result["unsafe_actions"] == 1 and result["unsafe_action_rate"] == 0.2
