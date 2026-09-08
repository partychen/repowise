# Pinned review: prepare, coordinate, finalize

Use the trusted installation and separate code/policy anchors established in
[setup](setup.md). The shared safety boundaries are in [SKILL.md](../SKILL.md).
This workflow never executes target code or publishes review comments.

## Prepare

For a GitHub PR, use its number or URL and the saved project binding:

```powershell
python -I $Runner --root $Target review --pr PR_NUMBER_OR_URL --trusted-ref POLICY_SHA
```

Authenticated metadata reads pin the reported head and unique local merge base
with the reported target-branch tip. The bounded title/body is frozen as
**untrusted change intent**, not policy or proof of execution. Missing local Git
objects are errors; use a separately authorized Git workflow to obtain them.
Explicit immutable revisions are also supported:

```powershell
python -I $Runner --root $Target review --repository owner/repo --base BASE_SHA --head HEAD_SHA --trusted-ref POLICY_SHA
```

BASE/HEAD belong to the target. `POLICY_SHA` belongs to the external memory's
independent Git repository. Signatures, repository identity, runtime bindings and
root separation establish trust; target ancestry does not. Linked worktrees or
shared Git metadata cannot supply independent policy.

Preparation freezes changed source, manifests, relevant project examples,
approved knowledge and the review plan. Its task identifies the code/policy
sources and production/evaluation run location. Check `requires_response`:
static-only runs may complete immediately; semantic runs need actual host
reasoning and finalization.

For current Rust changes, `--reference-query 'TOPIC DOMAIN'` and
`--reference-limit 3` attach bounded [reference packs](packs.md). Their source
hashes and omissions are retained. Pack IDs are not approved knowledge IDs, and
today's packs must not enter historical replay.

## Read the repository before judging it

`context_selection` records selected/requested paths, omissions and limitations.
Selection is heuristic, not a full call graph, dependency resolution or cfg
analysis. Missing examples do not prove that no repository precedent exists.
To capture a known module, test or configuration, prepare a new bounded task:

```powershell
python -I $Runner --root $Target review --repository owner/repo --base BASE_SHA --head HEAD_SHA --trusted-ref POLICY_SHA --context-path 'src\shared.rs' --context-path Cargo.toml
```

`--context-path` accepts repeated repository-relative paths and normalizes
Windows separators. It shares `--max-files` and `--max-bytes` with other captured
source. These limits do not bound Git's internal metadata/rename work. Never fill
gaps from a mutable worktree or execute project commands. Git reads disable lazy
fetches, replacement objects and optional write locks.

Assess the task's required dimensions:

| Dimension | Consider |
| --- | --- |
| `architecture` | Responsibilities, boundaries, dependency direction, state ownership |
| `reuse_frameworks` | Existing helpers, abstractions, versions and configuration |
| `contracts_types` | Caller-visible behavior, domain types, units, defaults, compatibility |
| `security` | Authentication, authorization, untrusted input, sensitive data and isolation |
| `errors_lifecycle` | Errors, cancellation, timeouts, retries, partial failure and cleanup |
| `concurrency_performance` | Blocking, locks, copying/allocation, backpressure and bounds |
| `tests_observability` | Behavioral scenarios, test conventions, logging and diagnostics |
| `idioms` | Applicable local syntax/type/API idioms, not cosmetic preference |
| `change_scope` | Affected callers/callees, exceptions, migrations and minimal repairs |

Prefer applicable same-subsystem implementations and established mechanisms.
Explain why a proposed new framework/helper is necessary. Consider deliberate
migrations, exceptions and defects in older code; neither prevalent code nor
generic best practices create policy authority. Performance findings need a
concrete path, workload and cost mechanism, not invented benchmarks.

## Coordinate host review

Follow the frozen plan and [role workflow](review-agents.md). Small reviews stay
with the coordinator; larger or risky changes may dispatch relevant specialists.
Every worker uses the same frozen context and records actual output. The
coordinator owns uncovered dimensions, cross-module reasoning, evidence review,
conflict resolution and traceable root-cause grouping.

Host capability/budget limits and failed/missing roles stay visible. The CLI
does not dispatch workers or call a provider, and a prepared plan is not an
execution record.

## Respond using the generated contract

`task.output_contract` is the authoritative machine shape. Copy its schema
version, `task_id` and `input_hash`; descriptive `instructions` are not response
fields. Use actual model metadata, or `"unknown"` for unavailable model and
prompt-version names. Provider must match the configured `model_provider`.
`examples\model-response.json` is a synthetic illustration, never a real response.

The coordinator supplies rule `assessments`, `repository_assessments` and
`findings`. Status is `checked`, `not_applicable` or `needs_context`, with a
nonempty rationale. Assess every requested semantic rule and repository
dimension; omissions and `needs_context` become gaps, not successful coverage.
Static-only acknowledgements cannot claim semantic assessments or execution.
For delegated reviews, also follow the task's role/reconciliation contract.

### Exact source references

A repository reference has `snapshot` (`base`/`head`), matching `commit`, captured
`path`, inclusive one-based `line_start`/`line_end`, and exact `text`. Normalize
source CRLF to LF and omit the final newline. BASE uses `old_path` for renames;
HEAD uses `path`. Validators only resolve captured source, never response paths
against the live filesystem.

Checked repository assessments require references. `architecture`,
`reuse_frameworks` and `idioms` require a BASE reference. Honest reasons explain
`not_applicable` and `needs_context`; do not invent precedent to satisfy a quota.

### Findings and exceptions

Each finding names a requested approved `knowledge_id`/`revision` with a checked
rule assessment, and supplies the contract's:

```text
basis, comparisons, evidence,
applicability_rationale, counterexample_checks,
impact, triggering_conditions, suggestion, uncertainty, verification
```

`basis: consistency` requires at least one exact BASE comparison. Explain its
applicability, the material consequence, and why an exception or migration does
not resolve the issue. A HEAD-only example cannot establish an old convention.
`basis: behavior` covers a directly supported behavior/contract defect and may
have no comparisons; do not use it to disguise a style complaint. Every supplied
comparison must still be an exact BASE reference.

`evidence` cites exact captured HEAD source (`path`, line range, `text`).
`counterexample_checks` is a nonempty list of actual inspection checks, not
boilerplate. `verification` is only `inspected` or `not_run`. Suggestions are
prose, never applied patches. Abstain when scope, consequences or exception
analysis lack evidence. Citation validation proves quotation identity, not the
correctness of a model's conclusion.

### Placement and fixed detectors

Inline findings require evidence overlapping a changed line. Other supported
concerns stay summary-only. Semantic findings retain `novelty: unknown`;
`evidence_pre_existing` records only whether the quotation existed before.
Changed callers can expose an old defect; changed lines alone do not prove cause.

The one fixed detector, `rust.forbidden-dependency.v1`, reads Cargo declarations,
including aliases and available workspace inheritance, and compares BASE/HEAD
edges. It does not run Cargo, resolve dependencies, evaluate cfg or support
explicit `package.workspace` pointers. Missing context stays a gap.
Static/hybrid rules need separately signed detector configurations. Manual
rules remain human coverage. Host decisions cannot suppress static results.

## Finalize and report

Save actual responses under the returned external `local_path`, then run:

```powershell
python -I $Runner --root $Target finalize --run-id RUN_ID --response RESPONSE_PATH
```

The CLI validates the frozen task and response, preserves detector results and
writes `response.json`, `report.json`, Markdown/escaped offline HTML reports,
and a hashed `completion.json`. An identical response is idempotent; changed
completion requires a new run. Static-only runs already have reports and accept
only strictly empty acknowledgements with explicit unknown model metadata.

Inspect role coverage, reconciliation, gaps and display truncation. Distinguish
complete processing with no supported findings, incomplete coverage, and findings
retained in machine artifacts but omitted from display. None proves correctness.
Actual detector runs record versions, result references and elapsed time; unknown
host token usage/cost remains unknown.

Runtime changes, including review orchestration/validation modules, invalidate
existing approval bindings. Have maintainers review and reapprove against the
new runtime; never rewrite signatures/hashes to make old approvals pass.
