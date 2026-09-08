# Connect a project and accumulate review knowledge

This is the default workflow after installation. Do the learning work in the host;
do not stop after printing a command or generating induction tasks.

## Select the project

Resolve the installed Skill directory and `$Runner` as described in [setup](setup.md).
Use the user's local project directory as `$Target` and the explicitly selected
GitHub `owner/repo` as the repository. If only a local project is specified, inspect
its existing origin URL read-only and ask if there is ambiguity; do not clone or
fetch as a side effect. If only a repository is specified, confirm which local
directory should hold its memory. Do not bind the Skill source directory by default.

```powershell
python -I $Runner setup
python -I $Runner --root $Target sync --repository owner/repo
```

The first sync initializes external project memory and saves its repository/path
binding. Read `storage_root` and `local_path` from the response; the default is
`~\.review-memory\projects\<name>-<path-hash>`. Nothing is created in the target
checkout, and any old target `.review` is ignored and left untouched.
Later calls use `sync` without `--repository`. A conflicting repository is rejected.
The default scope is all merged PR history; each command collects at most 20 PRs.
`--max-prs` allows 1-100. If the user selects a bounded initial history, use
`--since YYYY-MM-DD` (inclusive merged-at lower bound). The scope is persisted.
Closed-but-unmerged and open PRs are not collected by this learning workflow.

## Execute the learning loop

1. Read `sync` JSON even when exit code is 2. `batch_complete` with a remaining
   queue is resumable, not a completed historical sync. A nonempty `failures` list
   is an actual failure: disclose it and stop retries until it can be addressed.
2. Read `knowledge_path` to compare existing unapproved lessons before proposing
   duplicates. It is local evidence-backed reference material, not policy.
3. For each `pending_tasks` entry, resolve its `path` relative to `storage_root`. Read
   the task, validate referenced evidence identity/hashes, and inspect the evidence.
   Follow [learning](learning.md) and the full [knowledge schema](knowledge.md).
4. Perform the induction yourself in the host model. Preserve applicability,
   counterexamples, source IDs and uncertainty. No API key or separate model runner
   is required. Reuse a prior knowledge ID only when it really represents the same
   principle; do not claim semantic equivalence from matching titles.
5. Save the response at a task-specific path under the returned external
   `local_path`, then execute:

```powershell
python -I $Runner --root $Target propose --task TASK_PATH --response RESPONSE_PATH
python -I $Runner --root $Target status
```

`TASK_PATH` and `RESPONSE_PATH` must be resolved from the external storage root,
not the target, Skill installation or an unrelated shell cwd. `propose` paths are normal CLI file
arguments, so pass their resolved full paths when executing from elsewhere.

An empty candidate list is a valid inspected negative result; never generate it
just to mark work complete. A PR without feedback can legitimately yield no
generalizable lesson. Failed/malformed responses remain pending and do not accrue
knowledge. Existing saved proposals, including negative results, are recognized
by their task and input bindings when a session restarts.

Call `status` again to page through pending tasks (default 20, `--limit` up to 100).
When a batch's learning is finished and `remaining_pr_count` is nonzero, run `sync`
again without a new repository or scope. Continue within the host's budget.
At a budget limit, state the remaining counts and that the next invocation resumes.

## Persistent results

`status` rebuilds `.review/local/learning/index.json` from validated proposals,
retaining candidate paths, supporting PRs and evidence IDs. Identical principle,
applicability and execution fields are grouped without discarding supporting
proposals. Semantic merging still requires reasoning; the index does not invent
consensus, approvals or automatic rules.

The learning inbox is shared by `sync`, `bootstrap` and `harvest`. Selected PRs
and their saved proposals appear in `status` without a dummy sync or a new network
scan. `learning_pr_count` counts distinct PRs in that inbox; `collected_pr_count`
and `collection_complete` continue to describe full-history sync discovery.
Finishing a few selected PRs does not claim the entire repository history is done.

The index's `lineage` links knowledge IDs to revisions, entry IDs, candidate
paths and evidence. Unequal proposals with the same ID/revision remain separate
entries and are marked as alternatives, not silently merged or approved.
Malformed or missing tasks/proposals appear in `learning_gaps` and index
`coverage_gaps`; the result is partial, even if collection itself finished.

`collection_complete` describes the discovered PR snapshot; `learning_complete`
describes whether all collected tasks have host results. These do not imply
complete code-version evidence or an approved policy. Report both independently.
Do not stop at "synced" when `pending_task_count` is nonzero.

`next_actions` includes an `approval-queue` entry when knowledge is available.
It explicitly does not block learning. When the user asks to approve, execute
the [approval handoff](approval.md), show its readable preview and missing
requirements, and prepare selected requests. Do not just tell them to find an
index JSON or configure keys without explaining which knowledge they are reviewing.

The next fresh sync rescans PR list metadata and queues new/changed merged PRs.
Metadata is an incremental hint, not a guarantee that all feedback edits update
the PR. After a queue finishes, `sync --refresh` starts a full feedback refresh;
continue it with normal `sync` commands. Network/pagination failures never mark
uncollected PRs as processed. No scheduler, webhook or daemon is installed.

## Offline practice

Use `sync --repository example/project --fixture PATH` with an explicitly synthetic
fixture and a separate practice target. Repeat with the same `--fixture` for each
batch. Its state and index are isolated (`sync-fixture.json`, `fixture-index.json`);
inspect it with `status --offline`. Never present this as a live project sync.
Tasks, hashed input and evidence identities explicitly bind their `offline`
mode, so identical fixture text and live GitHub text cannot share provenance.
