# Temporal replay and independent adjudication

This is a **reduced single-arm D pipeline pilot**, not an A/B/C/D comparison.
It prepares the same production review engine against frozen historical inputs.
It does not train a model, automatically learn from feedback, or establish
measured benefits over other review methods.

## Dataset v1

Prepare JSON with exactly `schema_version: 1`, `timeline`, `windows`, and `events`.
`timeline` is `historical` or `simulated`; use the latter for constructed history.
Unknown keys are rejected at every schema object. Do not embed labels, answer
keys, feedback content, or extra context paths in the dataset.

`windows` has `train`, `dev`, `test`, each with RFC 3339 `start` and `end`.
Windows are ordered, nonoverlapping, and nonempty; start is inclusive and end is
exclusive. These names partition time, not model training.

Each review event contains:

```text
id, type: "review", available_at, repository, base, head, trusted_ref
```

An optional `group` may only be `"D"`. All three revisions must be full lowercase
commit SHAs already available in the target Git repository, not mutable branch
names or example placeholders. For each review, `trusted_ref` must be an ancestor
of or equal to `base`, as required by the production engine. IDs are unique safe identifiers. The dataset
contains 1–1000 chronological events with at least one review.

An optional feedback event has exactly:

```text
id, type: "feedback", available_at, review_id
```

It must follow its referenced review strictly in time. Feedback has no payload:
metadata is counted, but its content is not collected, ingested or used for
learning in this replay implementation.

```powershell
python -I $Runner --root $Target replay --dataset .\dataset.json
```

The command returns `replay_id`, `manifest_path`, `runs_root`, frozen `runs` and
limitations. Evaluation tasks live under
`.review\local\evaluation\REPLAY_ID\runs`, separate from production runs.

## Time and completion boundaries

The engine rejects commit timestamps later than review availability and selects
only signed policy eligible at the cutoff. Source version availability, approval
request/recording time, effective dates and expiry all matter. Do not backdate
new approvals to manufacture a historical policy.

A commit timestamp and a declared availability field are not independent proof
of historical observability. Public model-training contamination and missing
historical observations remain unknown. Simulated runs are not historical
performance evidence.

Preparation, manifests and scores expose `reconstruction_status`:
`simulation_only` for simulated timelines, `unverified` for declared historical
timelines, with `reconstruction_gaps`. No independently verified historical
policy provenance is implied. Contemporary approvals are eligible only at or
after their actual signed availability; exercise newly approved policies with
current-time **simulated** events instead of backdating requests. Tests with
stubbed signatures demonstrate cutoff logic, not historical provenance.

Static-only review tasks may finish during preparation. Host tasks with
`requires_response: true` require actual host reasoning and validated completion,
following the [review response contract](review.md). Do not copy an evaluation
task into production state to complete it. Preserve its original isolated run
directory and immutable manifest linkage.

For each run requiring a host response, save the response for that run's actual
task and finalize with both IDs returned by replay preparation:

```powershell
python -I $Runner --root $Target finalize --replay-id REPLAY_ID --run-id RUN_ID --response RESPONSE_PATH
```

`--replay-id` confines completion to
`.review\local\evaluation\REPLAY_ID\runs`; omitting it selects production runs.
There is no user-facing `--runs-root` flag. Complete every required host task,
then score the **same** replay ID with separate human labels:

```powershell
python -I $Runner --root $Target score --replay-id REPLAY_ID --labels .\labels.json
```

Scoring rejects unfinished runs, altered task/request linkage, mismatched
completion records and reports still awaiting responses. A finished report with
coverage gaps can be scored, but its incomplete coverage remains visible.

## Human labels v1

Labels are a separate JSON document, not part of model inputs. Top-level keys:

```text
schema_version: 1
replay_id: the actual frozen replay ID
adjudicator: {identity, kind: "human", independent: true}
cases: [...]
```

Each case has `case_id`, `reviewed_at`, `rationale`, boolean `normal_control`,
`issues`, and `judgments`. Review time must not precede task creation.
`issues` is a list of distinct `{id}` problem instances; a normal control has no
known problem instances.

Each judgment has `finding_id`, `disposition`, `rationale`, and optional
`issue_id` and `true_positive`. Dispositions are:

```text
valid, false_positive, waived, out_of_scope, obsolete,
deferred, duplicate, unjudged
```

Only `valid` with explicit `true_positive: true` and an actual matching
`issue_id` counts as a confirmed TP. A merely “valid” concern is not automatically
a confirmed defect. Do not coerce nonbinary dispositions into false positives.
The human identity and independence fields are attestations, not cryptographic
verification of the adjudicator.

```powershell
python -I $Runner --root $Target score --replay-id REPLAY_ID --labels .\labels.json
```

Scoring retains separate score artifacts for different label/report inputs.
The model must not create fictitious human identities or self-grade as the
independent adjudicator.

## Read the denominators

- Precision: confirmed `TP / (TP + FP)`, excluding duplicate matches to the same
  problem instance and unresolved/nonbinary dispositions.
- Recall: distinct matched labeled problem instances divided by all labeled
  problem instances, not agreement with the rule set.
- A zero denominator produces `null`, not a perfect score.
- Normal-control alert metrics distinguish any alert from a confirmed
  inappropriate alert. Unjudged control findings make the inappropriate-alert
  rate unknown.
- Unlabeled cases, unjudged findings, incomplete coverage and duplicate matches
  remain visible. Partial labels are not whole-dataset truth.
- Costs and token usage unavailable from the host remain unknown.

Metrics are returned separately for train/dev/test, not as a pooled result.
Report metrics by temporal split with their counts and limitations. Do not
present this pilot as a complete historical reconstruction or an experiment
proving knowledge-augmented review is better.
