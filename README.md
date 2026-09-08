# Review Memory

English | [Simplified Chinese](README.zh.md)

**RepoWise** is the project name; its Copilot skill and repository remain
`review-memory`. One project memory supports three workflows: **accumulate
engineering knowledge, review PRs, and implement features in the project's
existing architecture and conventions**. Memory is the shared foundation,
not an end in itself.

Project knowledge **grows like a snowball**: learn from merged PRs, preserve useful
lessons, apply approved revisions in later reviews, and refine them as new
human feedback arrives. This is a design goal, not a measured productivity claim.

**PR experience -> project memory -> knowledge-informed reviews -> new PR experience.**

[How it works](#how-it-works) | [Install](#install) | [First use](#first-use-select-your-project)

| Workflow | What the user receives | Execution boundary |
| --- | --- | --- |
| Accumulate knowledge | Resumable learning inbox, evidence-backed lessons, revisions and approval handoffs | Authenticated reads and external local storage |
| Review PRs | Project-aware findings, verified code comparisons and visible coverage gaps | Immutable code/policy reads; no target execution or publishing |
| Implement features | Actual host-authored changes, authorized checks and a recorded completion | User-authorized host coding tools; the CLI supplies context and records, not permission |

## Design philosophy

A PR review often explains more than a code change: why an interface has a certain
contract, which tradeoff matters to the team, or when an apparent anti-pattern is
actually valid. That reasoning should remain useful after the PR closes, rather
than having to be rediscovered in every review or chat session.

The unit of evolution is **the project's knowledge, not the model's weights**.
Each learning round compares new evidence with saved lessons. It can add support
for an existing principle, expose a counterexample, or motivate a revision or
retirement. The goal is not an ever-growing blacklist, but increasingly precise
knowledge of this project's decisions and their limits.

A useful lesson records its principle, applicable paths and context, exceptions,
valid and violating examples, and versioned evidence links. Repeated comments,
resolved threads, and merged PRs are evidence to interpret, not automatic team
consensus or permission to enforce a rule.

**Illustrative evolution, not actual repository evidence:** one PR motivates
preserving diagnostic context at request boundaries. Another reveals that
sensitive request values must be excluded. A later refactor centralizes context
handling, motivating a narrower rule. The memory should retain these observations
and support revised proposals, not turn the first comment into "log everything."

### Repository-first review

The reviewer must examine the project's architecture, existing frameworks and
helpers, contracts, error/resource conventions, concurrency/performance paths,
tests/observability, local idioms and change scope before recommending changes.
Generic best practices and personal syntax preferences are not repository policy.
Prefer a small repair fitting existing mechanisms; explain when a new mechanism
is actually necessary, and consider intentional migrations and valid exceptions.

This is enforced through task preparation and response validation, not only a
prose instruction. Bounded base/head project context is frozen with the task.
Host responses separately assess eight repository dimensions. A consistency
finding requires a verified BASE comparison as well as an approved rule and
current code evidence; an example newly added by the PR cannot establish an old
convention. Direct behavior defects need not invent a precedent. Omitted
assessments and missing context remain visible gaps. Citation validation proves
source identity, not the correctness of the model's conclusion.
See the [review contract](.github/skills/review-memory/references/review.md).

### What runs where

This is **one skill**, with responsibilities deliberately separated:

| Part | Responsibility |
| --- | --- |
| Skill instructions and workflow references | Guide the assistant through collection, learning, approval, and review. |
| Host assistant, such as Copilot | Read evidence, relate new observations to existing knowledge, propose lessons, and reason about current changes. |
| Bundled Python CLI | Collect through authenticated `gh` reads, preserve progress, validate evidence/signatures, prepare PR and feature context, run eligible fixed review detectors, and record local artifacts. It never edits target code, runs project commands or calls a model API. |
| External per-project memory directory | Keep knowledge, approvals and reports across sessions without adding files to the target or the installed skill. |
| Rust reference packs | Supply review questions and counterexamples, not repository policy or independently triggered skills. |

## How it works

```mermaid
flowchart LR
    PR["Merged PRs and review feedback"] --> Learn["Sync evidence and host learning"]
    Memory["Saved knowledge candidates"] --> Learn
    Learn --> Memory
    Memory -->|Maintainer signs and commits| Policy["Trusted approved policy"]
    Policy --> Review["Review later changes and produce a local report"]
    Code["Pinned project architecture, frameworks and idioms"] -->|Evidence, not policy| Review
    Policy --> Feature["Authorized host feature implementation"]
    Code -->|Project context| Feature
    Feature --> NewPR["User's code changes and separately requested PR"]
    NewPR -.->|Human feedback and merge, then learn again| PR
    Review -.->|Human feedback and merge, then sync again| PR
```

1. **Collect evidence.** `sync` reads merged PR reviews and discussions in bounded,
   resumable batches. It preserves evidence versions and available code context;
   missing source or failed API reads remain visible gaps.
2. **Relate, learn, and save.** The host reads both new evidence and the existing
   knowledge index, considers support and counterexamples, and proposes scoped
   lessons or revisions. `propose` validates and saves the result; `status` rebuilds
   the cumulative index. Exact matching lessons are grouped with their evidence;
   semantic merging requires host reasoning. A PR need not yield a new lesson.
3. **Approve deliberately.** Learning does not require approval. To use a lesson
   as review policy, a maintainer inspects and SSH-signs an exact revision through
   the [approval workflow](.github/skills/review-memory/references/approval.md).
   Verified knowledge and approval records must then be committed to the
   external memory's own trusted Git history, not the target branch.
   Static detectors need separate approval;
   the assistant never signs or grants itself authority.
4. **Reuse in a later review.** `review` pins base/head commits and a
   maintainer-selected trusted policy commit from the external memory repository. It uses
   eligible approved rules, runs separately approved fixed detectors where
   applicable, and prepares a repository-first host reasoning task with bounded
   comparable project code. `finalize` validates host output, including BASE
   comparison citations, and writes a local report with evidence and coverage gaps.
   Static-only runs can finish during preparation; manual rules still need humans.
5. **Implement a feature.** `feature` prepares the same project's approved
   knowledge and relevant baseline examples. After explicit user authorization,
   the host implements the actual change, preserves unrelated work, and performs
   the relevant authorized checks. `feature-finish` records actual host results,
   unknowns and blockers; a prepared brief is not a completed feature.
6. **Feed the next round.** People continue reviewing, discussing, and merging PRs.
   A later `sync` collects new or changed merged PR evidence and repeats the
   comparison with saved knowledge. New evidence can motivate further revisions;
   changing or retiring approved policy requires a new signed revision. A local
   model finding is not automatically ingested as accepted historical feedback.

The snowball is **persistent and invocation-driven, not an autonomous daemon**.
Installation starts no scheduler or webhook. Incremental PR metadata may miss
feedback edits; a full refresh after the pending queue finishes re-reads them.
Collection complete, learning complete, and policy approved are different states.

The current pilot does not automatically post PR comments, commit, push or decide
merges. The CLI never edits target code; feature edits belong to the separately
authorized host workflow. Review uses the trusted installed runtime and immutable
Git objects; it does not check out PR code or run target builds, tests, or hooks.
Its only fixed detector is `rust.forbidden-dependency.v1`, which inspects Cargo
dependency declarations rather than running Cargo. Missing evidence, expired
knowledge, and omitted model assessments remain gaps: "no findings" is not proof
of correctness. See [pilot scope and limitations](docs/pilot.md).

## Install

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

Requires Node.js/npx and Python 3.11+ with `venv` and `ensurepip`. For GitHub
synchronization, install GitHub CLI and sign in if you have not already:

```powershell
gh auth login
```

On first use, the assistant creates an isolated Python environment and installs
the bundled, hash-pinned pure-Python PyYAML wheel. **Dependency setup is offline:
it does not contact PyPI or require a compiler.** You do not need a separate Python
backend or model API key. Downloading the skill and syncing GitHub still require
their respective network access.

Keep `-g` for a personal installation if you want no Skill files in the target
either. Runtime commands never create a target `.review` or change its ignore files.

## Invoke the skill

**Open your target project and enter the following in your assistant's chat,
not in PowerShell:**

```text
Use review-memory to sync this project's PR reviews and build its review knowledge.
```

In Copilot CLI, you can explicitly select the skill with `/review-memory`:

```text
Use /review-memory to sync this project's PR reviews and build its review knowledge.
```

Requests such as "sync PR reviews", "learn from past reviews", and "accumulate team
review knowledge" help the assistant select the skill automatically. **Include
`review-memory` by name for the most explicit invocation.**

If Copilot CLI was already running when you installed the skill, run these inside
the CLI session:

```text
/skills reload
/skills info review-memory
```

For other hosts, select the corresponding agent during installation and reopen
the session if the new skill has not been discovered.

## First use: select your project

Enter this in your target project's session, replacing the repository URL:

```text
Use review-memory.
My project is https://github.com/acme/my-project.
Use the current workspace as its local directory.
Sync merged PR reviews, extract useful lessons, and save them as project knowledge.
```

`acme/my-project` is **the project you want to learn from**, not this skill's
installation repository. The assistant asks for missing project details rather
than silently choosing a repository or storage directory.

This starts the collection and learning stages above: the assistant saves the
project binding, prepares its runtime, processes evidence, and reports accumulated
knowledge and remaining work. It does not approve the resulting candidates.

The default batch size is 20 PRs. Large histories or session limits leave a saved
queue that a later invocation can resume. To limit the initial history:

```text
Use review-memory to learn from acme/my-project's PRs merged since 2026-01-01.
Use the current workspace as the local directory.
```

Selected `harvest` and `bootstrap` tasks share the same learning inbox and
knowledge index as `sync`; they do not require a full-history scan to become
visible. Knowledge lineage links revisions and their evidence while keeping
unequal alternatives separate and unapproved.

## Common requests

| Goal | What to say in the assistant's chat |
| --- | --- |
| Continue syncing and learning | Use review-memory to continue syncing this project and finish pending knowledge extraction. |
| Check progress | Use review-memory to show the remaining PRs and learning tasks for this project. |
| Browse accumulated knowledge | Use review-memory to summarize this project's knowledge and link to the supporting PRs. |
| Recheck historical feedback | Use review-memory to finish the pending queue, then fully refresh historical PR feedback. |
| Learn from one PR | Use review-memory to learn from acme/my-project PR #123 and save the review lessons. |
| Start knowledge approval | Use review-memory to show the pending candidates with evidence and prepare the ones I select for maintainer signing. |
| Review changes | Use review-memory to review these changes using this project's approved rules. |
| Review a GitHub PR | Use review-memory to review https://github.com/acme/my-project/pull/123 using the selected project policy. |
| Implement a feature | Use review-memory to implement this feature using the project's existing architecture and approved knowledge; preserve my existing changes and obtain authorization for the needed checks. |

For PRs, the host can use `review --pr NUMBER_OR_URL`; the CLI resolves the
local immutable revisions without checkout or fetch. For features, the host
uses `feature` to prepare context, then actually implements and verifies within
the user's authorization, and records the outcome with `feature-finish`.
Neither a context packet nor a written plan is a completed implementation.
See the [feature workflow](.github/skills/review-memory/references/feature.md).

## Where do I approve knowledge?

Ask the assistant to show the **approval queue**. It presents a readable preview
with the candidate's principle, source evidence, scope, exceptions and missing
requirements, rather than only pointing to an index JSON file. You select what to
review; the assistant prepares an exact unsigned request with your chosen owner,
signing identity and reason. Missing examples or evidence are not filled in merely
to make approval succeed.

Under the hood, `approval-queue` lists candidates and `prepare-approval` produces
the review/signing handoff. The maintainer configures signer trust and signs
outside the assistant; `approve` verifies and imports that signature. Approved
records must enter the **external memory repository's** trusted Git history before
policy review uses them. No target-repository commit is required.
See the [guided approval workflow](.github/skills/review-memory/references/approval.md).
Approval never blocks continued PR learning.

## Where knowledge is stored

Project state is outside both the target and the Skill installation:

```text
~\.review-memory\projects\<project-name>-<path-hash>\.review\
```

The canonical local path determines the namespace; the readable name is only a
label, and the saved GitHub repository binding is checked separately. Same-named
checkouts do not accidentally share knowledge. `doctor`, `sync` and `status`
return the exact `storage_root` and `local_path`; relative artifact paths are
relative to `storage_root`, **not the target checkout**.

The following paths are inside that external `storage_root`:

| Path | Contents |
| --- | --- |
| `.review/config.yaml` | Project binding |
| `.review/project.json` | Local target-path binding, excluded from policy Git history |
| `.review/local/state/sync.json` | Sync progress and queued PRs |
| `.review/local/raw/evidence` | Versioned PR evidence and availability metadata |
| `.review/local/proposals` | Learning tasks, host responses, candidates, and evidence associations |
| `.review/local/learning/index.json` | Accumulated **unapproved** knowledge index, reused in later learning |
| `.review/knowledge`, `.review/approvals` | Versioned knowledge and signed approval records for trusted policy |
| `.review/detectors` | Separately approved fixed-detector configurations |
| `.review/local/runs` | Local review tasks, findings, reports, and coverage gaps |
| `.review/local/features` | Feature context, host implementation/check reports, and completion records |

Updating or reinstalling the Skill leaves this independent data directory in
place. `--data-home` or `REVIEW_MEMORY_HOME` can select another absolute external
parent directory. The CLI leaves the target and its `.gitignore` unchanged;
authorized feature edits are performed by the host, not the CLI.
`.review/local` is ignored only inside the external policy repository.
There is one external-storage layout, with no target-local memory fallback.

## Further reading

- [Installation, storage, and distribution](docs/distribution.md)
- [Synchronization workflow](.github/skills/review-memory/references/sync.md)
- [Learning from history](.github/skills/review-memory/references/learning.md)
- [Knowledge structure and lifecycle](.github/skills/review-memory/references/knowledge.md)
- [Knowledge approval](.github/skills/review-memory/references/approval.md)
- [Code review](.github/skills/review-memory/references/review.md)
- [Feature implementation](.github/skills/review-memory/references/feature.md)
- [Temporal replay and independent evaluation](.github/skills/review-memory/references/replay.md)
