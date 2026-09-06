# Pinned review: prepare, reason, finalize

## Prepare from two independent anchors

Execute the fixed trusted installation against the target, with an independently
selected policy commit:

```powershell
python -I $Runner --root $Target review --repository owner/repo --base BASE_SHA --head HEAD_SHA --trusted-ref POLICY_SHA
```

The CLI reads commit-qualified content, not mutable worktree policy. The task
binds the runtime, repository, base/head commits, approved snapshot, selected
knowledge and bounded code context. PR-head changes to rules, signers or tool
scripts cannot authorize themselves.

For a current Rust review, add `--reference-query 'TOPIC DOMAIN'` and optionally
`--reference-limit 3` to freeze relevant [external knowledge](packs.md) into the task.
Pack contents and source versions become immutable task context, not approved rules.
Use them to check assumptions and exceptions; do not submit pack/check IDs as
`knowledge_id`. Reports retain source hashes and selection omissions. Historical
replay cannot opt into today's reference packs.
The trusted policy SHA must be an ancestor of or equal to the base SHA;
preparation verifies this relationship and rejects unrelated or PR-head-only
policy commits. Selecting a commit from the base ancestry still requires the
maintainer's independent authorization.

Preparation may finish a static-only run directly. If `requires_response` is
true, pass its task packet to the host Copilot; do not report that run as complete
before finalization.

## Fixed static coverage

The pilot has one allowlisted detector: `rust.forbidden-dependency.v1`.
It parses Cargo TOML declarations, including dependency aliases and available
workspace inheritance, and compares base/head edges. It does not run Cargo,
resolve the full dependency graph, compile code, or evaluate feature/cfg activation.
Explicit `package.workspace` pointers are unsupported; manifest and module
context is bounded. Unresolved context is a gap, not proof of absence.

Static/hybrid knowledge needs an independently signed detector configuration.
Only the known tool ID and validated parameters select trusted implementation.
No candidate-supplied command, plugin, regex program or generated source executes.
Manual knowledge remains a human-coverage requirement.

## Host response contract

Use the task's `output_contract`. The current review response has exactly:

- `schema_version: 1`, `task_id`, `input_hash` copied from the task.
- `model`: nonempty `provider`, `model`, `prompt_version` strings. Use `"host"`
  for the default host provider; semantic `provider` must exactly match the
  task's trusted `model_provider`. Use `"unknown"` for unavailable model name
  or prompt-version metadata, not to bypass the configured provider.
- `assessments`: one per requested semantic rule, each with `knowledge_id`,
  integer `revision`, `status` (`checked`, `not_applicable`, `needs_context`),
  and a nonempty `rationale`.
- `findings`: zero or more fully supported findings for a `checked` rule.

Assess every requested semantic rule. If an assessment is omitted, the validator
records a coverage gap rather than silently treating that rule as checked.

Each finding contains:

```text
knowledge_id, revision,
evidence: {path, line_start, line_end, text},
applicability_rationale, counterexample_checks,
impact, triggering_conditions, suggestion, uncertainty, verification
```

`path` uses the task's portable repository path. Lines are one-based and
inclusive. `text` must equal the exact joined HEAD source lines, without a final
newline, using LF separators (CRLF source is normalized). `counterexample_checks` is a nonempty array of actual checks, not a
boilerplate assurance. `verification` is only `inspected` or `not_run`.
Do not include the contract's descriptive `instructions` key in the response.
`examples\model-response.json` is a synthetic shape example, not an executable
task response: its identifiers must not be submitted or treated as approved
knowledge. Always respond to the actual prepared task.

Before claiming a finding:

1. Identify the approved rule revision and verify its scope.
2. Trace a concrete consequence using the supplied context.
3. Inspect the rule's exceptions and plausible valid alternatives.
4. Cite exact current source, separating fact from uncertainty.
5. If context is insufficient, use `needs_context` rather than guessing.

The model cannot claim execution, invent a knowledge ID, grant an exception,
silently suppress static findings, or add unsupported response fields.

## Placement is not causation

The validator can place findings inline only where evidence intersects a changed
line. Other supported findings are summary-only. Static declaration findings may
also be summary-only rather than inventing precise source coordinates.

Reports distinguish `introduced`, `pre_existing` and `unknown` where supported.
A line appearing in a diff does not prove the defect was introduced there.
Semantic findings retain `novelty: unknown`; `evidence_pre_existing` only records
whether the quoted source already existed. Changed callers can make unchanged
code newly problematic. Static dependency edges can be compared mechanically.
Exact source matching confirms a quotation, not the correctness of the model's
reasoning. Suggestions remain prose, never applied patches.

## Finalize and report

```powershell
python -I $Runner --root $Target finalize --run-id RUN_ID --response RESPONSE_PATH
```

Finalization validates task binding and response shape, retains fixed-detector
results, and renders local artifacts under `.review\local\runs\RUN_ID`.
It rejects mismatched or edited task packets and conflicting completion attempts.
An exact repeated response can be idempotent; a different response requires a
new run rather than rewriting the completed record.
Static-only runs already have a final report. An optional strictly empty
assessment/finding response with explicit unknown model metadata only acknowledges
that report; it must not be represented as a model execution.
Finalized artifacts include `response.json`, `report.json`, `report.md`,
`report.html`, and `completion.json`; the completion record binds its response
hash and report with a completion hash. HTML is escaped and uses an offline CSP.

Inspect the report's status, assessments, coverage gaps and display truncation.
Distinguish:

- complete processing with no supported findings;
- incomplete processing or missing coverage;
- findings retained in machine artifacts but omitted by display limits.

None means a proof of correctness. Return concise findings and artifact paths.
Actual detector invocations record their tool/version, result reference and elapsed
time separately from semantic costs. Host token usage and total cost remain unknown
when unavailable; the absence of a model invocation during a detector run is not
a claim of zero operational cost.
There is no automatic GitHub publishing, PR comment, patch, target test run,
or provider API invocation.
