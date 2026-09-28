"""Prove selected tests fail when safeguards are removed, in temporary copies.

The real source tree is never edited. Only pytest assertion failures count as
detected mutations; syntax errors, collection errors and timeouts do not.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    (
        "schema_extra_fields",
        "copilot/schema.py",
        'extra="forbid"',
        'extra="ignore"',
        "tests/test_grounding.py::test_schema_rejects_unsupported_fields_nested_keys_ids_and_coercions",
    ),
    (
        "confirmation_capability",
        "copilot/service.py",
        'if not hmac.compare_digest(row["token_hash"], token_hash(token)):',
        "if False:",
        "tests/test_execution.py::test_tampered_confirmation_cannot_move[token]",
    ),
    (
        "full_snapshot_check",
        "copilot/service.py",
        'self.store.snapshot(matches) != row["snapshot_hash"]',
        "False",
        "tests/test_execution.py::test_same_count_same_value_replacement_is_material",
    ),
    (
        "tenant_foreign_keys",
        "copilot/store.py",
        "PRAGMA foreign_keys = ON",
        "PRAGMA foreign_keys = OFF",
        "tests/test_execution.py::test_composite_foreign_keys_reject_cross_tenant_owner_and_stage",
    ),
    (
        "literal_evidence_boundary",
        "copilot/llm.py",
        "validate_evidence(intent, instruction)",
        "pass  # deliberately removed by the mutation probe",
        "tests/test_model_boundary.py::test_model_cannot_use_a_name_absent_from_instruction",
    ),
    (
        "clarification_evidence_boundary",
        "copilot/grounding.py",
        'if intent.decision == "refuse":',
        'if intent.decision != "move":',
        "tests/test_model_boundary.py::test_clarification_decision_cannot_bypass_constraint_validation",
    ),
]


def main():
    baseline = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *[m[4] for m in MUTATIONS]],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if baseline.returncode:
        print(baseline.stdout + baseline.stderr)
        raise SystemExit("Mutation probe requires a passing baseline.")
    results = []
    for name, filename, before, after, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="copilot-mutation-") as temporary:
            checkout = Path(temporary)
            shutil.copytree(
                ROOT / "copilot", checkout / "copilot", ignore=shutil.ignore_patterns("__pycache__")
            )
            shutil.copytree(
                ROOT / "tests", checkout / "tests", ignore=shutil.ignore_patterns("__pycache__")
            )
            path = checkout / filename
            source = path.read_text()
            if source.count(before) != 1:
                raise SystemExit(
                    f"Mutation {name} needs review after source changes; refusing an ambiguous patch."
                )
            path.write_text(source.replace(before, after, 1))
            environment = {**os.environ, "PYTHONPATH": str(checkout)}
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", test],
                cwd=checkout,
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
            )
            output = run.stdout + run.stderr
            killed = run.returncode == 1 and "FAILED " in output
            results.append(
                {
                    "name": name,
                    "test": test,
                    "detected": killed,
                    "exit_code": run.returncode,
                    "output": output[-6000:],
                }
            )
            print(f"{name}: {'DETECTED' if killed else 'NOT DETECTED'}", flush=True)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "baseline": "passed",
        "detected": sum(r["detected"] for r in results),
        "total": len(results),
        "mutations": results,
    }
    destination = ROOT / "artifacts/mutation-report.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {destination.relative_to(ROOT)}")
    return 0 if all(r["detected"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
