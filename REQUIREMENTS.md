# Pipeline Copilot: requirements and acceptance checklist

Written before implementation on 2026-09-28 from all six pages of
`SDE2-3-Opportunities-AI-Take-Home.md.pdf`. This is the development contract;
`REQUIREMENTS-CHECK.md` will record the final evidence against it.

## Priority and scope

The hard problem is turning an instruction into a safe, grounded bulk stage move,
then measuring semantic correctness. Spend effort on the action boundary,
grounding, adversarial inputs, realistic evaluations, and a defensible design.
Keep the foundation small. The assignment explicitly excludes a UI beyond a CLI
or bare endpoint, authentication, a background job engine, deployment/CI,
multi-turn memory beyond one clarification, other CRM actions, RAG, custom fields,
fine-tuning, and voice. These will not be built.

## Required behavior

| ID | Requirement | Acceptance evidence planned |
| --- | --- | --- |
| F01 | Python or TypeScript implementation, runnable entry point, tenant selected explicitly | CLI integration smoke test and documented commands |
| F02 | Seeded tenant-scoped workspaces, ordered pipeline stages, owners, opportunities with name, exact money, status, stage and timestamps | Database constraints and seed assertions |
| F03 | `count(filter)`, `sum(filter)`, and `bulkMove(filter, targetStage)` returning a job ID | Query/transaction tests and end-to-end example |
| G01 | Convert natural language into only supported stage, owner, status, value-range and date-range filters plus a target stage | Strict schemas, semantic evals, unsupported-field tests |
| G02 | Every entity is grounded in the selected workspace; ambiguous and nonexistent names never guessed | Deterministic resolver, exact/ambiguous/unknown cases |
| G03 | Explicit, implemented clarification policy and threshold; at most one clarification round | Policy documentation and answer validation tests |
| G04 | Relative dates use a controlled clock; boundary semantics and timezone documented | Frozen-clock month/quarter/rolling-window tests |
| G05 | Invalid model output gets bounded validation/repair and a clear terminal failure | Malformed JSON, extra fields, invented entities, exhausted retry tests |
| S01 | Every action requires a preview with readable filter, count, total value, samples and explicit confirmation | End-to-end CLI demonstration and direct-bypass tests |
| S02 | Confirmation is bound to the exact previewed plan; regenerated plans invalidate old confirmations | Token/plan/workspace/revision tampering tests |
| S03 | Define material change; reject stale matches atomically with execution | Same-count replacement, value/version change, expiry and race tests |
| S04 | Implement count/value blast-radius limits and an additional confirmation or narrowing requirement | Threshold boundary and large-move tests |
| S05 | Tenant isolation is enforced independently of the model, including plan replay across tenants | Tenant predicates, composite foreign keys, cross-workspace tests |
| S06 | User-controlled record names cannot inject instructions into the plan | Model input boundary, poisoned-owner/record tests, terminal escaping |
| M01 | At least 150 instructions with independently specified semantic outcomes | Versioned eval dataset, category counts, corpus checks |
| M02 | Cover clean instructions, ambiguity, relative dates, unsupported/impossible requests, adversarial data and instructions | Per-category cases and reports |
| M03 | Harness measures exact structured-plan match, clarification precision/recall, and unsafe-action rate per category | Machine-readable results plus documented metric definitions |
| M04 | Run evaluations more than once; report variance honestly | At least three runs; distinguish repeated live calls from replay |
| M05 | Report p50/p95 latency, input/output tokens per instruction, model/settings/hardware and cost | Recorded response usage, per-case timings and aggregate reports |
| M06 | Model slowness/outage/garbage never becomes a confident partial plan | Total deadline, bounded retry, fault-injection tests |
| D01 | Main seed: approximately 20,000 opportunities, 12 uneven stages, 25 owners, 18 months | Reproducible seed statistics |
| D02 | Near-duplicate owners (Priya Sharma, Priya S., Priyanka Rao), confusing stages (Proposal Sent/Review) | Resolver/evaluation fixtures |
| D03 | Two smaller tenants with different owners and stages, plus malicious names | Seed data and isolation/injection tests |
| R01 | Name actual model version and justify model/framework choices | Model manifest and design rationale |
| R02 | One documented command sets up, seeds and runs full evals on a clean machine | Fresh-environment bootstrap verification |
| R03 | Committed genuine model responses support keyless record/replay; live mode accepts reviewer credentials | Request-hashed cassettes, provenance, live adapter and replay-miss test |
| T01 | Focused tests protect plan binding, schema, tenants and injection, with meaningful failure sensitivity | Safety regression suite and selected mutation probes |
| A01 | Git repository with genuine incremental commit history | Milestone commits made as work is completed |
| A02 | README covers setup, seed, replay/live eval, model/settings, implemented scope and limitations | Documentation command verification |
| A03 | DESIGN.md, approximately 2-3 pages, covers model/code boundary, validation, grounding thresholds, confirmation/staleness, injection/tenant holes, first scaling bottleneck and ranked next week | Design review |
| A04 | EVALS.md covers composition, metrics/variance, latency/tokens/cost, model/hardware and five instructive failures with responses | Results generated from actual runs; no invented measurements |
| A05 | 60-90 second screen recording: clean end-to-end action, clarification, rejected injection, running eval harness | Playable recording plus reproducible demo script |
| A06 | Final audit against this file after development; document any gap without claiming completion | REQUIREMENTS-CHECK.md with commands and artifact links |

## Initial implementation decisions to validate

- Use Python, SQLite, a CLI, and a small explicit orchestration pipeline.
- Use integer minor currency units and UTC timestamps. The seeded main workspace
  uses INR; no conversion or cross-currency sums.
- Freeze the interpretation clock to `2026-09-28T12:00:00Z`. Use a separate real
  clock for confirmation expiry and request deadlines.
- Treat the model as an untrusted intent extractor. Application code owns entity
  resolution, date arithmetic, query compilation, risk checks, and execution.
- Keep opportunity/owner catalog text out of the model context. Resolve mentions
  against a tenant-scoped catalog in deterministic code after extraction.
- Store immutable plans server-side. Confirmation supplies an opaque plan ID and
  capability, never a client-authored filter. Recheck the entire matching snapshot
  and commit the move in one transaction.
- Prefer conservative, documented ambiguity and stale-data rejection over silent
  approximation. Report false refusals and semantic errors in the evals.
- Preserve raw model recordings and their actual provenance. Local model execution
  is available on this machine; hosted live evaluation must remain configurable.

## Assumptions and explicit product semantics

One pipeline per workspace; opportunity status is independent of stage and a
stage move changes only the stage, stage-entered/updated timestamps and version.
All supplied filters are conjunctions unless an operation is explicitly supported.
"Month" in calendar expressions differs from a rolling 30-day window. Ambiguous
date fields or quarter years must be clarified or handled under a stated policy.
No credentials or signed-in browser data will be committed. A workspace selector
is an isolation boundary for this assignment, not authentication.
