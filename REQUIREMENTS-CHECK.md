# Assignment acceptance audit

Audited on 2026-09-29 against all six pages of the assignment and the
[requirements written before development](REQUIREMENTS.md). The 33 checklist
items have evidence below. The submission contains the requested CLI bulk-move
copilot, its tests, reproducible evaluation evidence and the required demo.

## Current verification

- **184 tests pass**, including 32 date-evidence regressions. Removing safeguards
  is detected by **9/9 mutation probes**. [Tests](tests/test_model_boundary.py),
  [mutation evidence](artifacts/mutation-report.json).
- **720/720 exact outcomes** across 240 published instructions and three fresh
  runs, with zero unsafe actions, 471 completed transactions, **558 fresh provider
  calls** and zero reused calls. All fresh response takes 3-5 are shipped.
  [Live measurement](artifacts/eval-date-fix-live.json),
  [matching replay](artifacts/eval-date-fix-fresh-replay.json),
  [freeze](evals/date-fix-manifest.json).
- Default takes 0-2 also reproduce 720/720 outcomes with zero unsafe actions.
  These supply the bootstrap and demo. All **1,116** current-prompt responses for
  takes 0-5 remain committed. [Default replay](artifacts/eval-date-fix-replay.json).
- The [75-second H.264 demo](artifacts/demo.mp4) shows the four required scenes:
  clean preview and confirmation, ambiguity, refused injection and the 240-case
  evaluation. Its [transcript](artifacts/demo.cast) and
  [provenance](artifacts/demo-recording.json) identify the same app and corpus.
- [Submission verification](artifacts/submission-verification.json) records the
  cleanup checks, fresh-install bootstrap, both replay sets and unchanged
  application, corpus, recordings and demo fingerprints.

Both published corpus splits have been used for development. The assignment does
not require a perfect score; the measured 100% applies to this regression corpus.
The initial **90% challenge result** remains unchanged in
[its original report](artifacts/eval-challenge-live.json).

## Date-filter correction

Each extracted field must use a distinct literal occurrence in its grammatical
role. An owner or stage called `Month` cannot consume the word in `created last
month`. Overlapping or omitted evidence triggers bounded repair or failure.
The original simulated model response omitted the date and produced a 91-record
preview where independent SQL selected 9; confirmation changed 82 records outside
the requested month. That response now creates no confirmation capability and
causes zero writes. Preserving or repairing the date moves exactly the intended
9 records, including after owner clarification.
[Before](artifacts/date-collision-audit.json),
[after](artifacts/date-collision-fixed.json),
[regression tests](tests/test_model_boundary.py).

These deterministic boundary tests use disposable databases and are separate
from the live-model score. The fixes also retain passive move wording, UTC
`today`/`yesterday`, comparator repair and supported `for Leo Wu` ownership.

## Submission contents and historical evidence

Superseded reports, exploratory probes and obsolete prompt recordings have been
removed from the checkout and Git history at the submitter's request. Required
application changes and the incremental development history are preserved.
The five required instructive failures remain in [EVALS.md](EVALS.md), backed by
[their exact case results and all eight complete provider responses](artifacts/historical-failures.json).
This includes the unsafe baseline action, rather than concealing it behind the
current passing results. Current live/replay reports are retained without changes;
the application, corpus and demo are unchanged by this packaging cleanup.

## Requirement-by-requirement evidence

| ID | Status | Implementation and evidence |
| --- | --- | --- |
| F01 | Met | Python CLI with mandatory workspace selection for scoped commands. [Public CLI tests](tests/test_cli.py) exercise separate processes, persistence and exit codes. |
| F02 | Met | [SQLite schema](copilot/store.py) has tenant keys, ordered stages, owners, exact money, statuses and timestamps. [Deterministic seed](copilot/seed.py). |
| F03 | Met | `Store.count`, `Store.sum`, and the private `Copilot._bulk_move` provide synchronous selection/move and job ID. Public access to the move is through validated confirmation. [Execution tests](tests/test_execution.py). |
| G01 | Met | A closed [Intent/Filter/Plan contract](copilot/schema.py) restricts fields. Literal evidence is bound to distinct occurrences in their semantic roles; overlapping or omitted constraints trigger repair/failure. |
| G02 | Met | Name/status resolution uses the selected tenant catalog; exact names, collisions and missing names are covered in [grounding tests](tests/test_grounding.py) and the semantic corpus. |
| G03 | Met | Unique full name, otherwise unique prefix of at least four characters; collisions ask. One round accepts only listed IDs. [Policy and failure modes](DESIGN.md), [clarification tests](tests/test_grounding.py). |
| G04 | Met | Clock capture, fixed eval clocks, UTC boundaries, calendar/rolling semantics and rollover tests pass. The date/name collision now fails closed; complete/repaired dates move the exact intended records. [Date tests](tests/test_dates.py), [model-boundary tests](tests/test_model_boundary.py). |
| G05 | Met | Provider schema, strict local validation and at most one repair. Missing/partial/borrowed evidence is checked for move and clarification decisions. Exhausted repair creates no capability. [Model-boundary tests](tests/test_model_boundary.py). |
| S01 | Met | Preview shows readable predicates, target, count, exact total, five samples and expiry. Plan/show/clarify never move deals. [CLI workflow tests](tests/test_cli.py), [recorded end-to-end action](artifacts/demo.mp4). |
| S02 | Met | Saved immutable plan, canonical digest, hashed random capability, tenant/operation/revision binding. Regeneration invalidates before inference. Tamper and asynchronous regeneration-race tests in [execution tests](tests/test_execution.py). |
| S03 | Met | Any matching row, membership or catalog change is material. Full fingerprints and `BEGIN IMMEDIATE` make recheck and execution atomic. Same-count/same-value replacement, insertion, field changes, expiry, concurrency and rollback are tested. |
| S04 | Met | Above 500 matches or INR 10m requires a separate challenge; above 5,000 or INR 100m requires narrowing. Exact boundaries, second-stage drift, hard blocks and no-op capability omission are tested in [execution tests](tests/test_execution.py). |
| S05 | Met | SQL predicates, composite foreign keys and all capability lookups carry workspace scope. Cross-tenant replay fails even with otherwise valid credentials. [CLI](tests/test_cli.py) and [database/confirmation tests](tests/test_execution.py). |
| S06 | Met | No catalog or sample record is included in any model request. Poisoned names remain data; terminal/bidi controls are escaped. [Boundary tests](tests/test_model_boundary.py), [rendering tests](tests/test_grounding.py), 34 adversarial-record evals. |
| M01 | Met | [240 unique instructions](evals/cases.jsonl): 180 original regression cases and 60 initially frozen challenge cases, subsequently used to guide fixes. Gold-label generators do not import the production interpreter. [Protocol](evals/README.md). |
| M02 | Met | Six required categories: clean 46, ambiguous 40, dates 46, unsupported 40, adversarial instructions 34, adversarial records 34. Ten new challenge cases per category. [Composition](EVALS.md). |
| M03 | Met | [Harness](copilot/evaluation.py) reports normalized-plan/outcome accuracy, clarification precision/recall, actual unsafe execution and wrong previews per category and split. An independent oracle verifies preview totals, required execution and whole-store changes. [Scorer tests](tests/test_evaluation.py). |
| M04 | Met | Three fresh full-corpus runs, with per-run/per-category variance and zero reused calls. [Current live JSON](artifacts/eval-date-fix-live.json), [current replay](artifacts/eval-date-fix-fresh-replay.json), [initial challenge](artifacts/eval-challenge-live.json), [analysis](EVALS.md). |
| M05 | Met | Live planning p50/p95, input/output/total tokens, call counts, hardware, model digest and local API cost are reported. Hardware/energy cost is explicitly unmeasured. [EVALS.md](EVALS.md). |
| M06 | Met | Per-call and total wall deadlines, two attempts maximum, bounded output, transport/auth failure policy, unknown timeout usage, no fallback parser. [Fault tests](tests/test_model_boundary.py), [provider-contract tests](tests/test_transport.py). |
| D01 | Met | Atlas: 20,000 opportunities, 12 uneven stages, 25 owners. Creation dates span March 2025–September 2026. [Seed statistics](artifacts/seed-statistics.json). |
| D02 | Met | Priya Sharma / Priya S. / Priyanka Rao and Proposal Sent / Proposal Review are seeded and evaluated. [Seed](copilot/seed.py), [cases](evals/cases.jsonl). |
| D03 | Met | Harbor has 160 records, Cedar 120; their stage/owner catalogs differ. Malicious owner text, record instructions, ANSI and bidi names are seeded. |
| R01 | Met | Mistral 7.2B Q4_0 identified by verified manifest digest, Ollama version and settings. Pinned hosted adapter snapshot; no agent framework. [Manifest](artifacts/model-manifest.json), [rationale](DESIGN.md). |
| R02 | Met | The documented bootstrap installs pinned dependencies, seeds all tenants, runs 184 tests and replays all 720 default case-runs. The cleaned submission is verified from a fresh source export. [Submission verification](artifacts/submission-verification.json). |
| R03 | Met | Keyless exact-request replay and live adapters work. The checkout includes all 1,116 genuine recordings for current-prompt takes 0-5. Five historical failures retain eight complete provider responses in one evidence file. Publication/corruption tests protect evidence and prevent overwrites. |
| T01 | Met | 184 tests cover safety, transactions, races, schema, date grounding, model boundaries, scorer errors, recording integrity and public CLI behavior. Nine mutations in isolated copies demonstrate failure sensitivity. |
| A01 | Met | Real incremental history starts with requirements in `ad3754a`, then foundation, tests, an unsafe baseline, fixes and measured evidence. The final correction and demo are committed with their tests and recordings. The Git repository is the delivery artifact; an external portable archive is not part of this finalization. |
| A02 | Met | [README.md](README.md) covers setup, seed, normal use, replay/live/record, own-key mode, clocks, thresholds, model/settings, supported scope and limits. |
| A03 | Met | [DESIGN.md](DESIGN.md) is approximately three pages of design discussion: authority boundary, schema/repair, thresholds, staleness, injection/tenant holes, first scaling bottleneck and ranked next week. |
| A04 | Met | [EVALS.md](EVALS.md) gives all requested numbers and five observed historical failures with original model responses and fixes. Selected failure evidence is available offline with original measurement summaries and complete responses. |
| A05 | Met | The replacement [75-second recording](artifacts/demo.mp4) shows all four required scenes and the current 240-case corpus. Transcript, metadata, code/corpus hashes and visual checks agree. |
| A06 | Met | This post-development audit maps every original requirement to the shipped implementation, reproducible commands and durable evidence. |

## Deliberate limits

The hosted adapter's HTTP contract is tested, but no hosted live measurement is
claimed. The local model supplies the real measurements. No load test at 100
instructions/second or 20 million records is claimed; DESIGN.md identifies the
expected first bottlenecks and remedies.

The corpus is synthetic and shares authorship with the implementation.
Conservative evidence rules can refuse valid prose, and arbitrary English can
still be misinterpreted. Tenant isolation assumes a trusted caller-selected
workspace; authentication and protection against a user controlling the local
process or database are outside the assigned scope.
