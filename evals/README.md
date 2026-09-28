# Evaluation corpus protocol

`cases.jsonl` contains 240 unique instructions in six categories. The splits
identify corpus origins and are reported separately:

- `regression`: the original 180 template-assisted cases used during development.
  The previous `development` and `holdout` tags were misleading because both were
  used for iteration; all those cases now carry the `regression` label. Original
  reports retain their historical hashes and results.
- `challenge`: 60 separately authored cases in `challenge.jsonl`, ten per category.
  Their instructions, literal expected outcomes and measured code were frozen in
  `challenge-manifest.json` before the first provider call. The original freeze
  and complete raw report remain unchanged for provenance.

The same implementer authored both sets. The first challenge run measured
previously unmeasured instructions, not independent human annotation or a blind
production benchmark. Three runs repeat the same 60 instructions. The six observed
misses subsequently guided fixes to passive wording, date repair, relative calendar
days and unsupported requests. **Both splits are now development regression
evidence.** The split names identify where cases originated; current measurements
do not claim an unseen test set. A new claim about unseen inputs needs a new set.

`scripts/build_challenge_set.py` specifies gold labels without importing production
parsing, resolution or date arithmetic. After freezing, it refuses to write a
different corpus under the same manifest. `scripts/build_eval_set.py` regenerates
the regression cases and appends the frozen challenge cases. Neither script runs
as part of evaluation.

Every evaluation uses a fresh database clone. The optional input `clock` is
separate from expected output, defaults to `2026-09-28T12:00:00Z`, and controls the
clone's interpretation and transaction timestamps. Date fixtures put rows just
outside and exactly on interval boundaries; some intentionally differ in their
created, entered and updated timestamps. Gold output never reaches the model.
The independent oracle checks full-store changes, preview count/value/move totals,
and whether an eligible plan actually completed its confirmation transaction.

Reproduce all recorded cases:

```bash
./run eval --mode replay --runs 3 --output artifacts/local/replay.json
./run eval --mode replay --split challenge --runs 3 --output artifacts/local/challenge.json
```

Run fresh inference (this repeats a published corpus; it is no longer an unseen set):

```bash
./run eval --mode live --provider ollama --split challenge --runs 3 --output artifacts/local/challenge-live.json
```

The original first measurement used `--mode record --cases evals/challenge.jsonl`
with three fresh takes and no `--resume`. `artifacts/eval-challenge-live.json`
preserves that raw measurement. `challenge-manifest.json` describes that historical
freeze, including its then-false `used_for_tuning` flag; it is not rewritten to
describe later development. The subsequent fixes use prompt/request version
`extract.v3`, with new request hashes and recordings. Superseded prompt versions
are excluded from the final submission. The five required failure examples,
including all their original responses and
repair attempts, are also available offline in
[historical-failures.json](../artifacts/historical-failures.json).

The final date/name fixes have their own `date-fix-manifest.json` and
measurements in `artifacts/eval-date-fix-live.json` and
`artifacts/eval-date-fix-replay.json`. They preserve the same prompt and dataset.
The latest live measurement used fresh takes **6, 7 and 8**, without reuse.
All 558 request payloads, response texts and token counts match the retained
takes **0, 1 and 2**. Only those **558** recordings are shipped; the redundant
second set and its additional replay report were removed. Retained recordings
keep their original provider timestamps and timings. The live report contains
the latest measured live latency. Run `./run eval --mode replay --runs 3` to
reproduce the semantic results on current code, using the bootstrap/demo defaults.
Recorded provider timings and local replay timings are reported separately.

The `Month` collision and related overlapping/partial evidence scenarios are
deterministic model-boundary tests with simulated responses, including actual
confirmation transactions. They are not added to the live-model accuracy
denominator. The audit's before/after evidence remains in `artifacts/`, and focused
regressions cover the supported owner wording as well as omitted date evidence.
The later timestamp-context regressions cover owner/source/target names such as
`Created`, `Updated` and `Entered`, with and without a separate timestamp clause,
including clarification and independent SQL checks of confirmed changes. These
tests also stay separate from the live-model denominator. The checkout keeps only
the recordings needed by its documented offline evaluation and demo.
