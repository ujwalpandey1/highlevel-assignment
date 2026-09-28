# Final acceptance audit

Audited on 2026-09-28 against all six pages of the assignment and the
[requirements written before development](REQUIREMENTS.md). All 33 checklist
items have implementation or delivery evidence below. Scope is the requested CLI
bulk-move copilot; there is no UI, authentication, background job engine, deployment
configuration, RAG, or unrelated CRM action.

## Executed verification

The final application revision is `33e08c6`. A fresh `git archive` export started
with **no virtual environment and no database**. Running exactly
`bash scripts/bootstrap.sh` installed the hash-pinned dependencies, seeded all
three tenants, passed **105 tests**, and reproduced **540/540 exact evaluation
outcomes with zero unsafe actions**, using the three committed model takes.
The clean run used Python 3.13; development and live measurements used Python 3.11.
Initial dependency installation required package-registry access; model replay
used no API credentials or model-provider calls.

The final replay's structured outcomes and actual changed-record sets match the
live measurement for every case. Live measurement: 180 distinct instructions,
three fresh runs, 411 provider calls, zero reused calls, 360 executed confirmation
paths. The perfect regression score is on an iteratively used, template-assisted
corpus; it is not a blind generalization result. [Measured results](EVALS.md).

| Check actually run | Result | Durable evidence |
| --- | --- | --- |
| Clean `bash scripts/bootstrap.sh` | Exit 0; 105 tests; all 3 × 180 replay cases correct | [Verification receipt](artifacts/verification.json), [full clean replay](artifacts/eval-clean-replay.json) |
| Ruff lint and format checks | Both passed | [Verification receipt](artifacts/verification.json) |
| Remove six selected safeguards in temporary source copies | 6/6 detected by failing regression tests | [Mutation report with failures](artifacts/mutation-report.json) |
| Verify every shipped final cassette | All 411 request/response hashes and live provenance valid | [Verification receipt](artifacts/verification.json) |
| `./run schema` | Only supported fields; extra properties forbidden | [Exported API schemas](artifacts/api-schemas.json) |
| Verify seed and timestamp-format optimization | All 20,280 opportunity rows unchanged, including names and dates | [Counts, distributions and full-row fingerprint](artifacts/seed-statistics.json) |
| `ffprobe` plus visual inspection at 10/24/38/74 seconds | H.264, 1280 × 900, 75 seconds; all four scenes legible | [Video](artifacts/demo.mp4), [terminal capture](artifacts/demo.cast), [capture provenance](artifacts/demo-recording.json) |

## Requirement-by-requirement evidence

| ID | Status | Implementation and evidence |
| --- | --- | --- |
| F01 | Met | Python CLI with mandatory workspace selection for scoped commands. [Public CLI tests](tests/test_cli.py) exercise separate processes, persistence and exit codes. |
| F02 | Met | [SQLite schema](copilot/store.py) has tenant keys, ordered stages, owners, exact money, statuses and timestamps. [Deterministic seed](copilot/seed.py). |
| F03 | Met | `Store.count`, `Store.sum`, and the private `Copilot._bulk_move` provide synchronous selection/move and job ID. Public access to the move is through validated confirmation. [Execution tests](tests/test_execution.py). |
| G01 | Met | A closed [Intent/Filter/Plan contract](copilot/schema.py), literal evidence checks and [deterministic grounding](copilot/grounding.py) produce only the supported AND filters and destination. |
| G02 | Met | Name/status resolution uses the selected tenant catalog; exact names, collisions and missing names are covered in [grounding tests](tests/test_grounding.py) and the semantic corpus. |
| G03 | Met | Unique full name, otherwise unique prefix of at least four characters; collisions ask. One round accepts only listed IDs. [Policy and failure modes](DESIGN.md), [clarification tests](tests/test_grounding.py). |
| G04 | Met | Fixed interpretation clock, UTC half-open intervals, calendar/rolling semantics and disclosed quarter-year assumption. Boundary, invalid-date and rollover tests in [grounding tests](tests/test_grounding.py). |
| G05 | Met | Provider schema plus strict local validation; at most one repair. Duplicate keys, invented mentions, extra fields, garbage and exhaustion are tested. Clarification outputs pass the same evidence checks. [Model-boundary tests](tests/test_model_boundary.py). |
| S01 | Met | Preview shows readable predicates, target, count, exact total, five samples and expiry. Plan/show/clarify never move deals. [CLI workflow tests](tests/test_cli.py), [recorded end-to-end action](artifacts/demo.mp4). |
| S02 | Met | Saved immutable plan, canonical digest, hashed random capability, tenant/operation/revision binding. Regeneration invalidates before inference. Tamper and asynchronous regeneration-race tests in [execution tests](tests/test_execution.py). |
| S03 | Met | Any matching row, membership or catalog change is material. Full fingerprints and `BEGIN IMMEDIATE` make recheck and execution atomic. Same-count/same-value replacement, insertion, field changes, expiry, concurrency and rollback are tested. |
| S04 | Met | Above 500 matches or INR 10m requires a separate challenge; above 5,000 or INR 100m requires narrowing. Exact boundaries, second-stage drift, hard blocks and no-op capability omission are tested in [execution tests](tests/test_execution.py). |
| S05 | Met | SQL predicates, composite foreign keys and all capability lookups carry workspace scope. Cross-tenant replay fails even with otherwise valid credentials. [CLI](tests/test_cli.py) and [database/confirmation tests](tests/test_execution.py). |
| S06 | Met | No catalog or sample record is included in any model request. Poisoned names remain data; terminal/bidi controls are escaped. [Boundary tests](tests/test_model_boundary.py), [rendering tests](tests/test_grounding.py), 24 adversarial-record evals. |
| M01 | Met | [180 unique, versioned instructions](evals/cases.jsonl), with gold labels authored separately before measurement. [Generator](scripts/build_eval_set.py) does not import the production interpreter. |
| M02 | Met | Six required categories: clean 36, ambiguous 30, dates 36, unsupported 30, adversarial instructions 24, adversarial records 24. [Composition](EVALS.md). |
| M03 | Met | [Full-path harness](copilot/evaluation.py) measures exact normalized plans, exact outcomes, clarification precision/recall, actual unsafe execution and wrong previews, per category. An independent oracle checks whole-store changes. |
| M04 | Met | Three fresh local inference runs, with per-run/per-category variance. Separate replay evidence is labelled correctly. [Live JSON](artifacts/eval-live-local.json), [analysis](EVALS.md). |
| M05 | Met | Live planning p50/p95, input/output/total tokens, call counts, hardware, model digest and local API cost are reported. Hardware/energy cost is explicitly unmeasured. [EVALS.md](EVALS.md). |
| M06 | Met | Per-call and total wall deadlines, two attempts maximum, bounded output, transport/auth failure policy, unknown timeout usage, no fallback parser. [Fault tests](tests/test_model_boundary.py), [provider-contract tests](tests/test_transport.py). |
| D01 | Met | Atlas: 20,000 opportunities, 12 uneven stages, 25 owners. Creation dates span March 2025–September 2026. [Seed statistics](artifacts/seed-statistics.json). |
| D02 | Met | Priya Sharma / Priya S. / Priyanka Rao and Proposal Sent / Proposal Review are seeded and evaluated. [Seed](copilot/seed.py), [cases](evals/cases.jsonl). |
| D03 | Met | Harbor has 160 records, Cedar 120; their stage/owner catalogs differ. Malicious owner text, record instructions, ANSI and bidi names are seeded. |
| R01 | Met | Mistral 7.2B Q4_0 identified by verified manifest digest, Ollama version and settings. Pinned hosted adapter snapshot; no agent framework. [Manifest](artifacts/model-manifest.json), [rationale](DESIGN.md). |
| R02 | Met | The exact documented one-command bootstrap passed in a fresh export, including all 105 tests and full three-take eval. [Verification receipt](artifacts/verification.json). |
| R03 | Met | All genuine model recordings are committed; exact-request replay is keyless and fails on a miss. Local and own-key OpenAI live adapters are implemented. Existing takes cannot be overwritten; atomic publication/corruption tests protect evidence. |
| T01 | Met | 105 focused tests cover safety, transactions, races, schema, grounding, injection, providers, recording integrity and public CLI behavior. Six destructive mutations in isolated copies demonstrate meaningful failure sensitivity. |
| A01 | Met | Real incremental history starts with requirements in `5e9ac6d`, then foundation, tests, an unsafe measured baseline, fixes and new measurements. The portable archive includes `repository.bundle` to preserve that history. |
| A02 | Met | [README.md](README.md) covers setup, seed, normal use, replay/live/record, own-key mode, clocks, thresholds, model/settings, supported scope and limits. |
| A03 | Met | [DESIGN.md](DESIGN.md) is approximately three pages of design discussion: authority boundary, schema/repair, thresholds, staleness, injection/tenant holes, first scaling bottleneck and ranked next week. |
| A04 | Met | [EVALS.md](EVALS.md) gives all requested numbers and five observed historical failures with original model responses and fixes. Earlier failing reports remain intact. |
| A05 | Met | [75-second recording](artifacts/demo.mp4): clean action, owner clarification, rejected injection, full 180-case harness running. It is an actual terminal capture rendered to video; disposable scripted confirmations and replay mode are labelled. |
| A06 | Met | This post-development audit maps every original requirement to the shipped implementation, reproducible commands and durable evidence. |

## Deliberate limits, not hidden completion claims

The hosted adapter's HTTP contract is tested, but no hosted live measurement is
claimed: there was no hosted API key. The local model supplies the real measurements
required by the assignment. No load test at 100 instructions/second or 20 million
records is claimed; DESIGN.md identifies the expected first failures and remedies.

The language corpus is synthetic and was used during development. Conservative
evidence rules can refuse valid prose, and arbitrary English can still be
misinterpreted. Tenant isolation assumes a trusted caller-selected workspace;
authentication and protection against a user who controls the local process or
database are outside the assigned scope. These boundaries are explicit in the
design rather than implied by the passing tests.

No required deliverable is deferred. The submission repository is
[ujwalpandey1/highlevel-assignment](https://github.com/ujwalpandey1/highlevel-assignment).
The separate portable archive also retains the source and full Git history.
