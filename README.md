# Pipeline Copilot

A natural-language instruction becomes a grounded bulk-move preview. Only an
explicit confirmation of that exact preview can change records. The model cannot
choose a tenant, issue SQL, invent filter fields, or execute a move.

The implementation concentrates on the assignment's hard parts: semantic
correctness, ambiguity, stale previews, bounded model failures, tenant isolation,
and an evaluation that actually exercises the transaction. There is deliberately
no web UI, authentication, deployment setup, or background job framework.

The latest evaluation scores **100%: 720/720 exact outcomes** across 240 published
instructions and three fresh runs, with **zero unsafe actions**. This is a
development regression score. The full results and five development failure
examples are documented in [EVALS.md](EVALS.md).

The final date fixes are verified by **205 tests**, **10 detected safeguard mutations**,
and three fresh model runs. The shipped takes 0-2 reproduce the full evaluation
with `./run eval --mode replay --runs 3` on the corrected code.
The replacement [75-second demo](artifacts/demo.mp4) shows
the current 240-case evaluation.

The [2026-09-29 assignment audit](REQUIREMENTS-CHECK.md) found date/name collisions
outside that corpus. Regression tests now prevent owner/stage names from consuming
a date condition or choosing its timestamp field. For example, an owner named
`Created` does not resolve an unspecified timestamp to `created_at`.

## Start here

Prerequisites: **Python 3.11+**, a POSIX shell, and internet access for the first
dependency installation. No API key, model download, Ollama installation, or
database server is needed for replay.

Clone the submission repository:

```bash
git clone https://github.com/ujwalpandey1/highlevel-assignment.git
cd highlevel-assignment
```

From the repository root, one command installs hash-pinned dependencies into a
virtual environment, seeds the data, runs safety tests, and runs all 240 evaluation
cases three times using the included model recordings:

```bash
bash scripts/bootstrap.sh
```

Then run the complete demonstration:

```bash
./run demo
```

The demo uses a disposable database and explicitly identifies its scripted
confirmations. Normal `plan` commands never execute automatically.

Read [DESIGN.md](DESIGN.md) for the decisions and tradeoffs,
[EVALS.md](EVALS.md) for actual measurements and failures, and
[REQUIREMENTS-CHECK.md](REQUIREMENTS-CHECK.md) for the final acceptance audit.
The [requirements checklist](REQUIREMENTS.md) was committed before development.
The [terminal recording](artifacts/demo.mp4) shows the required four scenarios.

## Use the copilot

The three workspace IDs are `atlas`, `harbor`, and `cedar`. Every tenant-scoped
command requires `--workspace`; it must appear before the subcommand.

```bash
./run seed
./run --workspace atlas catalog
./run --workspace atlas stats --filter '{"stage_id":"qualified","status":"open"}'
./run --workspace atlas plan \
  'Move open deals owned by Asha Verma from Qualified to Proposal Sent worth under INR 25000.'
```

On a fresh seed this example previews 46 opportunities, INR 531,817.25, five sample
records, the resolved filter, target, expiry, plan hash, and confirmation token.
Review the preview, then copy its three exact values:

```bash
./run --workspace atlas confirm \
  --plan PLAN_ID --token CONFIRMATION_TOKEN --hash PLAN_HASH
```

The response includes a job ID and actual moved count. Retrying the same confirmed
plan returns the same job. `./run --workspace atlas show --plan PLAN_ID` retrieves
the immutable preview; `./run --workspace atlas audit` shows the audit trail.
Add the global `--json` option for machine-readable output.

For an ambiguous instruction:

```bash
./run --workspace atlas plan "Move Priya's deals from Proposal Sent to Negotiation."
./run --workspace atlas clarify --operation OPERATION_ID --answer owner=priya-sharma
```

Clarification offers real workspace IDs and accepts exactly those choices, in one
round. It produces a new preview, not an action. Multiple ambiguous slots are asked
together; supply one `--answer SLOT=ID` for each. No model call interprets the answer.

Regenerate with `plan 'NEW INSTRUCTION' --replace OPERATION_ID`. This invalidates
the previous confirmation **before** the new model request, even if that request
fails. A new `plan` without `--replace` starts an independent operation.

If a preview exceeds 500 matches or INR 10 million, `confirm` issues a second
challenge. Review its count and destination, then repeat the command with
`--challenge CHALLENGE_TOKEN --ack 'EXACT ACKNOWLEDGEMENT'`. More than 5,000 matches
or INR 100 million requires a narrower filter. Boundaries are strict `>`.

## Seed, clocks, and semantics

`./run seed` is idempotent. `./run seed --reset` explicitly resets the selected
local database. Use `--db PATH` before a subcommand to select a separate database.
Do not reset a database containing work you want to retain.

The reproducible seed contains 20,000 Atlas opportunities, 12 unevenly populated
stages, 25 owners, and roughly 18 months of timestamps, plus 160 Harbor and 120
Cedar opportunities. It includes near-duplicate names and malicious record/owner
text. Monetary values and sums are integer paise; the workspace currency is INR.

Normal `plan` requests capture **current UTC once, before inference**. That date
reference stays fixed through clarification, preview and confirmation. Use
`plan --clock ISO_TIMESTAMP` to reproduce a particular date; timezone offsets are
normalized to UTC. The preview displays the reference when a date filter is present.
The seed, demo and original eval cases explicitly use **2026-09-28T12:00:00Z**;
challenge cases also exercise fixed month/year rollovers and leap-day boundaries.
Evaluation clones use their case clock for writes and expiry as well. Normal
preview/challenge expiry and actual write timestamps use real UTC time.
Calendar months/quarters are half-open UTC ranges. `today` and `yesterday` use
UTC midnight-to-midnight calendar days, including leap days and year rollovers.
Rolling days end at the interpretation clock, exclusively. A duration of
"a month" means 30 days. A quarter without a year uses the clock's year and states
that assumption in the preview. An unspecified timestamp field triggers a question.
For ISO dates, `on` includes the entire UTC day, `before` excludes that day,
`after` starts at the following midnight, and `since` includes that day's start.
`between` includes both named calendar days.
Literal evidence is tied to one distinct occurrence in its grammatical role.
An owner or stage named `Month` cannot account for `month` in a separate date
condition. Overlapping or ambiguous repeated evidence and omitted/partial date
conditions get at most one repair, then fail without an executable preview.
For a name that resembles a duration, use `owned by` or quote it instead of an
ambiguous unquoted `for` clause.
Timestamp detection also excludes the validated owner/stage occurrences. Names
such as `Created`, `Updated` and `Entered` remain names; a missing timestamp still
asks a question, and a separate explicit timestamp retains its meaning.

All filters are conjunctions. Value ranges are inclusive in minor units; strict
comparisons add/subtract one paisa. Moving a deal changes its stage, stage-entered
and updated timestamps, and version. It **does not change status**. Matches already
in the target count toward the preview but remain unchanged and are reported
separately. Empty/no-op previews have no confirmation capability.

## Replay, live, and record modes

**Replay is an exact recording replay, not an offline natural-language model.**
It accepts recorded instructions with the recorded prompt/schema/settings. A miss
returns `replay_miss`; it never falls back to a guessed plan or silently calls a
provider. `evals/cases.jsonl` contains all 240 evaluation instructions:
**180 original regression cases + 60 originally frozen challenge cases**.
The challenge cases guided fixes, so both splits now provide development
regression evidence. The current implementation scores **100%** across both
splits; measured results and failure analysis are in [EVALS.md](EVALS.md).
Neither corpus is an independently annotated blind benchmark.
Replay reuses the recorded literal extraction; date resolution still uses the
explicit evaluation clock or the clock captured for a normal request.

```bash
# Full offline regression, three independently recorded takes
./run eval --mode replay --runs 3 --output artifacts/local/replay.json

# Inspect the original challenge cases as a separate regression slice
./run eval --mode replay --split challenge --runs 3 --output artifacts/local/challenge-replay.json

# Real local inference for arbitrary instructions
./run --workspace atlas plan 'Move deals owned by Priya Sharma from Proposal Sent to Negotiation.' --mode live

# Fresh local evaluation; live mode does not reuse or write cassettes
./run eval --mode live --provider ollama --runs 3 --output artifacts/local/live-local.json

# Hosted evaluation using your own key, read from the environment
export OPENAI_API_KEY='YOUR_KEY'
./run eval --mode live --provider openai --runs 3 --output artifacts/local/live-openai.json

# Capture a separate recording set without overwriting the shipped evidence
./run eval --mode record --provider openai --runs 3 \
  --recordings artifacts/local/openai-recordings --output artifacts/local/record-openai.json
```

For local live mode, start Ollama and install the desired model. The measured model
is **Mistral 7.2B Q4_0**, manifest SHA-256
`3944fe81ec14610e0852c3d915768ee8d507ea541387fdfcbbf9edaa0c757734`.
The adapter verifies this digest for its default `mistral:latest` tag. If your tag
points at different weights, choose an explicit `--model NAME` and measure that
model separately. Do not treat that run as reproducing the recorded model.

The hosted adapter defaults to **`gpt-4.1-mini-2025-04-14`**, a pinned snapshot with
strict structured-output support. Its request/response contract is tested; no
hosted live results are claimed because no hosted API key was available during
development. Model details and hardware are in
[artifacts/model-manifest.json](artifacts/model-manifest.json).

Settings: temperature 0, local seed 42, 400 maximum output tokens, two attempts at
most, 25 seconds per call and 52 seconds overall. `--timeout` changes the per-call
bound; the total becomes `2 * timeout + 2`. A single retry handles transient errors
or a validation repair with application-generated feedback. A missing date
comparator also includes the previous schema-valid extraction as context; other
errors re-extract from the instruction. The complete replacement is validated
again; previous fields are never accepted automatically. Exhaustion produces
an explicit unavailable result, without a preview.

Recordings are addressed by hashes of the complete instruction, prompt, schema,
model identity, settings, and repair feedback. Each file retains the actual raw
response, usage, timing, timestamp and checksums. `--take N` selects a take;
`--resume` resumes an interrupted **record** run and reports reused calls. A fresh
measurement must have zero reused calls. Never commit recordings of private data.
Record files are published atomically without overwriting an existing take;
corrupt or incomplete files produce an explicit error.

## Verification and repository map

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check copilot tests scripts
.venv/bin/python scripts/mutation_probe.py
./run schema
```

The eval process exits nonzero if it detects an unsafe action. The scorer also
checks preview counts/sums independently and requires eligible plans to complete
their confirmation transaction. A safely refused or
unavailable instruction still counts as an accuracy failure when a plan was
expected. Null precision/recall values mean the denominator was zero, not 100%.
Repeated runs are shown as case-runs alongside the unique-instruction count.

| Path | Responsibility |
| --- | --- |
| `copilot/llm.py`, `prompts.py` | Bounded provider calls, strict extraction, genuine record/replay |
| `copilot/grounding.py` | Literal evidence, entity resolution, money and date semantics |
| `copilot/schema.py` | Closed intent and executable filter contracts |
| `copilot/service.py` | Clarification, immutable preview, capability checks, atomic execution |
| `copilot/store.py`, `seed.py` | Tenant-scoped SQL, integrity constraints, deterministic dataset |
| `copilot/evaluation.py` | Independent action oracle, cloned databases, semantic metrics |
| `evals/` | Regression/challenge labels, freeze manifest and genuine model recordings |
| `artifacts/` | Current measurements, five historical failure examples, mutation evidence and demo |
| `tests/` | Safety, concurrency, grounding, provider and replay regressions |

## Deliberate limits

This is a local take-home system, not a production service. The workspace flag is
not authentication; the process and database files are trusted. Only one owner,
source stage, status and date field are supported per AND filter. OR, exclusions,
rankings, deal-name/contact/custom-field filters, multiple actions and other CRM
mutations are refused. Conservative evidence checks can refuse legitimate prose.
Unfamiliar unsupported phrasings may exhaust validation and report unavailable;
the scorer counts these as misses when an explicit refusal was expected. Chained
moves, unsupported renewal/due dates and rankings receive explicit refusals.
There is no claim that a finite corpus proves semantic safety for all language.
Scaling priorities and remaining holes are explicit in DESIGN.md and EVALS.md.

The checkout retains the current measurements, the **558 model recordings** used
by takes 0-2, and the five required historical failure examples with their complete
provider responses. The latest live calls produced identical request payloads,
response text and token counts, so a second recording set is unnecessary for
replay. The retained recordings preserve their original provider metadata;
latest live timings come from the live report. Exploratory probes and duplicate
recordings are excluded from the submission.
