# Pilot scope and acceptance

## What this version demonstrates

This is an evidence-backed governance and review pipeline, not a claim of
universal Rust expertise. It includes read-only historical collection, host
induction, signature-backed revision approval, one fixed Cargo dependency
detector, bounded role-based host review, local reports, and a reduced temporal replay.

The versioned reference packs broaden the questions a reviewer can ask. They do not
expand the approved policy set or the executable detector allowlist.
Current-review tasks may explicitly freeze selected external packs. Historical
replay excludes today's pack library; reference availability is not reconstructed.

## Evaluate the workflow before its findings

Use a controlled repository and independently inspected fixtures. Check:

1. Fresh initialization has no signer keys or active approved knowledge.
2. Unsigned candidates cannot run, and changing a maturity field is insufficient.
3. Knowledge approval alone cannot authorize an unapproved detector.
4. PR-head changes to tools, policies and signers cannot change the trusted run.
5. Altered task IDs, evidence, signatures and runtime hashes fail closed.
6. Missing context remains a visible coverage gap.
7. Review responses cannot claim project execution or replace static results.
8. CLI output is local-only; it creates no target patch or publication. Only the
   separately authorized feature host edits target code.
9. Replays reject future inputs and preserve frozen run linkage.
10. Scoring requires separate human labels, not model self-grading.
11. Project commands leave target files, ignore rules and Git refs unchanged.
12. Candidate review and signing preparation produce a readable handoff without
    activating knowledge, signing, changing signer trust or committing automatically.
13. Policy Git objects come from the external memory's own repository; code
    objects come from the read-only target. Overlapping roots are rejected.
14. Review tasks freeze bounded existing project examples and expose context
    selection/omissions; explicit context paths never read the mutable worktree.
15. A consistency finding needs exact BASE comparison evidence, not an example
    newly introduced by the PR. Direct behavior defects need no invented precedent.
16. Checked repository dimensions require source references; missing dimensions
    or necessary context remain gaps. A correct quotation does not prove reasoning.
17. The response cannot claim project conventions as new approved knowledge,
    invent framework execution, or suppress fixed detector results.
18. A PR number or URL resolves only within the bound repository, using a unique
    local merge base; missing objects never trigger target checkout or fetch.
19. Feature preparation is not implementation or authorization. Only the
    explicitly authorized host edits code and executes permitted checks.
20. Feature completion validates its frozen task and host report without executing
    command text. Failed checks, missing assessments and remaining work stay visible.
21. Worker reports bind the original task, role and scope. A planned, failed or
    unavailable role cannot silently count as completed review.
22. Same-root-cause display groups retain evidence and provenance. Distinct
    issues sharing a source location are not overwritten or merged automatically.

Run the repository's existing automated suite from the trusted development
checkout:

```powershell
python -m unittest discover -s tests -v
```

Do not describe the suite as passing unless the command actually passed in the
current environment. These tests are distinct from executing commands inside
an untrusted target project.

## Interpreting an experiment

Replay currently supports **one D pipeline arm**, not implemented A/B/C/D
comparisons or measured improvement over a baseline. Train/dev/test are temporal
partitions, not model-training operations. Feedback-event metadata does not
trigger automatic harvesting or learning during replay.

Prepare with `replay --dataset`, complete each required host task using
`finalize --replay-id REPLAY_ID --run-id RUN_ID --response RESPONSE_PATH`,
then `score --replay-id REPLAY_ID --labels LABELS_PATH`. Use the same replay ID
throughout; its tasks and completion artifacts remain in the isolated evaluation
directory. The ordinary finalize command without `--replay-id` targets production
runs. See the replay reference for complete commands and schemas.

Use independent human adjudication with explicit problem instances, controls,
and nonbinary dispositions. Report denominators, unlabeled cases, unjudged
findings and incomplete coverage alongside precision and recall. Do not treat
waivers, deferrals, duplicates or obsolete concerns as automatic false positives.

Simulated timestamps must be labeled simulated. Git commit times and declared
availability do not independently prove what was historically observable.
An LLM may already have seen public repository material; contamination is
unknown, not eliminated by temporal splitting.

## Honest limits

- Rust manifest declaration analysis is not Cargo resolution or compilation.
- Host source reasoning is fallible even after exact evidence validation.
- Role routing is heuristic, and worker execution is host-reported. Subagents
  do not guarantee better coverage or grant independent model-provider access.
- Root-cause grouping and conflict resolution are coordinator judgments, not
  mechanically proven semantic equivalence.
- Repository-first context selection is bounded and heuristic, not a full call
  graph or a guarantee that the best comparable implementation was found.
- The host's classification of behavior versus consistency and the relevance
  of a comparison remain judgments, not mechanically established facts.
- One repository's approved conventions do not transfer automatically.
- Independent PR counts do not prove independent agreement.
- External documentation may differ from the target's version and features.
- Runtime hashes and signatures support integrity, not a tamper-proof audit log.
- Token usage, latency and costs unavailable from the host stay unknown.
- `raw_retention_days` is metadata only; no automatic cleanup job or prune
  command exists, and expiration is not enforced.
- Synthetic examples are never real evidence, approved policy or measured results.
- The synthetic collection fixture demonstrates input plumbing, not evaluation
  evidence or a measured review outcome.

Before production adoption, establish who approves policy and tool updates, who
manages signer trust, where sensitive artifacts live, and how knowledge expires.
