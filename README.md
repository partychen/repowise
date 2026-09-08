# RepoWise

English | [Simplified Chinese](README.zh.md)

> Your team's experience. Your agent's next change.

**RepoWise** gives GitHub Copilot, Claude Code and Codex persistent,
evidence-backed project knowledge. It learns from PR reviews, applies approved
lessons to new reviews, and guides feature implementation within the repository's
architecture and conventions.

[Installation](#installation) | [Workflows](#workflows) | [Review roles](#review-roles) |
[Project memory](#project-memory) | [Trust model](#trust-model) | [Documentation](#documentation)

## Capabilities

| Workflow | Result |
| --- | --- |
| Learn from PRs | Resumable collection, scoped lessons, supporting evidence and revision history |
| Review changes | Repository-aware findings, verified source citations and explicit coverage gaps |
| Implement features | Agent-authored changes, authorized checks and recorded completion |

Knowledge evolves through new PR evidence: a later review can reinforce a lesson,
reveal an exception or motivate a revision. The evolving asset is the project's
knowledge, not the model's weights. Each lesson retains its scope, counterexamples
and provenance; only maintainer-approved revisions become review policy.

## Requirements

| Requirement | Purpose |
| --- | --- |
| GitHub Copilot CLI, Claude Code or Codex CLI | Run the skill and perform reasoning |
| Node.js with `npx` | Install the skill |
| Python 3.11+ with `venv` and `ensurepip` | Run the bundled CLI |
| Git | Read immutable source and policy commits |
| Authenticated GitHub CLI (`gh`) | Collect PR metadata, reviews and discussions |
| OpenSSH with SSH signature support | Sign and verify policy approvals |

Authenticate GitHub CLI before accessing your repositories:

```powershell
gh auth login
```

On first use, the agent prepares an isolated Python environment from the bundled,
hash-pinned PyYAML wheel. Dependency setup is offline; no separate runtime
installation or model API key is required. See [setup](.github/skills/repowise/references/setup.md)
for environment and Git requirements.

## Installation

Choose your agent below. Run the terminal commands from your local project
directory, then enter the skill request in the agent's conversation.
`-g` installs the skill for your user account across projects.

### GitHub Copilot CLI

**Terminal**

```powershell
npx skills add partychen/repowise --skill repowise -a github-copilot -g
copilot
```

**Skill request**

```text
Use /repowise to sync this project's PR reviews and build project knowledge.
```

[GitHub Copilot skill reference](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills)

### Claude Code

**Terminal**

```powershell
npx skills add partychen/repowise --skill repowise -a claude-code -g
claude
```

**Skill request**

```text
/repowise Sync this project's PR reviews and build project knowledge.
```

[Claude Code skill reference](https://code.claude.com/docs/en/skills)

### Codex CLI

**Terminal**

```powershell
npx skills add partychen/repowise --skill repowise -a codex -g
codex
```

**Skill request**

```text
$repowise Sync this project's PR reviews and build project knowledge.
```

[Codex skill reference](https://developers.openai.com/codex/skills/)

## Workflows

The following requests work in each agent's conversation. Use your agent's
skill invocation above or include `repowise` by name.

### 1. Connect a project and learn

Identify the GitHub repository and its existing local checkout:

```text
Use repowise for https://github.com/acme/my-project.
Use the current workspace as its local directory.
Sync merged PR reviews, extract useful lessons and save them as project knowledge.
```

Replace `acme/my-project` with your target repository. The agent binds the project,
collects review evidence, compares it with existing lessons and saves validated
knowledge proposals. The result includes accumulated knowledge and remaining work.

The default scope is merged PR history, processed in batches of 20. Progress
persists between sessions. To select an initial time range:

```text
Use repowise to learn from this project's PRs merged since 2026-01-01.
```

| Task | Request |
| --- | --- |
| Resume learning | Use repowise to continue syncing this project and finish pending learning tasks. |
| Inspect progress | Use repowise to show the remaining PRs and learning tasks. |
| Browse knowledge | Use repowise to summarize project knowledge with links to supporting PRs. |
| Learn one PR | Use repowise to learn from PR #123 and save its review lessons. |
| Refresh feedback | Use repowise to finish the pending queue, then fully refresh historical PR feedback. |

Learning runs on request; installation starts no scheduler or webhook.
Collection, learning and policy approval have separate completion states.

### 2. Approve project knowledge

```text
Use repowise to show pending knowledge candidates with their evidence,
scope and exceptions. Prepare signing requests for the revisions I select.
```

The agent presents readable previews and prepares unsigned approval requests.
A maintainer configures trusted signers, signs the selected revisions and commits
the verified records to the external memory's Git repository. That policy commit
can then be selected for reviews.

Learning does not require approval. Knowledge and detector approvals are separate;
the agent does not sign or grant trust. See the
[approval workflow](.github/skills/repowise/references/approval.md).

### 3. Review a pull request

With approved knowledge available, provide the PR and a trusted policy commit:

```text
Use repowise to review https://github.com/acme/my-project/pull/123.
Use POLICY_SHA from the project's external memory repository as the trusted policy commit.
```

Replace `POLICY_SHA` with the maintainer-selected commit. RepoWise resolves the
PR's immutable BASE/HEAD from local Git objects, captures bounded project context
and prepares the review. Code objects must already be available locally.

Reviews assess architecture, existing frameworks and helpers, contracts, security,
resource lifecycle, concurrency, tests, idioms and change scope. Consistency
findings require exact BASE comparisons; behavior defects require concrete
consequences and triggering conditions. All policy findings reference approved
knowledge revisions.

The result is a local report with findings, evidence, assessments and coverage
gaps. Markdown groups related findings with expandable supporting evidence;
JSON retains the underlying records and provenance.

### 4. Implement a feature

```text
Use repowise to implement cursor-based pagination for the existing list endpoint.
Follow the project's architecture and applicable approved knowledge.
You may edit the relevant files and run the existing targeted checks.
Preserve my existing changes. Do not commit or push.
```

The agent prepares project context, performs the authorized edits and checks,
and records the actual result. Features can start without approved policy;
missing policy coverage is recorded explicitly. Completion includes changed
files, check outcomes and remaining work.

## Review roles

One coordinator owns final coverage and reconciliation. Specialist roles provide
focused analysis against the same frozen code, project context and approved knowledge.

| Role | Focus |
| --- | --- |
| Architecture | Module boundaries, dependency direction, existing mechanisms and local idioms |
| Logic and contracts | Business invariants, types, caller behavior, compatibility and migration scope |
| Security | Authentication, authorization, untrusted input, sensitive data and isolation |
| Reliability | Error propagation, cancellation, retries, partial failure and resource cleanup |
| Concurrency and performance | Races, blocking, contention, backpressure and resource bounds |
| Tests and observability | Regression scenarios, meaningful assertions and diagnostic signals |

Automatic planning keeps small changes with the coordinator and selects relevant
roles for larger changes. Explicit role selection supports focused reviews:

```text
Use repowise to review PR #123 with architecture, security and reliability roles.
Run at most two workers concurrently, then reconcile their findings.
```

The default concurrency limit is three workers. Execution uses the host agent's
available subagent tools; single-agent and serial execution are recorded separately.
Missing or failed roles remain coverage gaps.

The coordinator validates reasoning, resolves disagreements and groups findings
by root cause. Original evidence and rejection reasons remain auditable. Distinct
defects at the same location remain separate, and static results cannot be suppressed.
See [review coordination](.github/skills/repowise/references/review-agents.md)
for routing, CLI options and response contracts.

## Project memory

Each local checkout has an external memory directory:

```text
~\.repowise\projects\<project-name>-<path-hash>\.review\
```

The canonical checkout path determines its namespace; the GitHub repository
binding is validated separately. Commands return the exact `storage_root` and
`local_path`. Artifact paths are relative to `storage_root`.

| Storage-root-relative path | Contents |
| --- | --- |
| `.review/config.yaml`, `.review/project.json` | Project configuration and local checkout binding |
| `.review/local/state/sync.json` | Collection progress and queued PRs |
| `.review/local/raw/evidence` | Versioned review evidence |
| `.review/local/proposals`, `.review/local/learning/index.json` | Candidates, responses, lineage and unapproved knowledge |
| `.review/knowledge`, `.review/approvals`, `.review/detectors` | Knowledge revisions, signatures and detector approvals |
| `.review/local/runs` | Review tasks, worker records and reports |
| `.review/local/features` | Feature context and completion records |
| `.review/local/evaluation` | Replay tasks and independent adjudication |

Set `REPOWISE_HOME` or the CLI's `--data-home` option to select another external
parent directory. Updating the skill preserves project memory. The CLI creates
no memory files or ignore rules in the target checkout.

## Trust model

| Component | Responsibility |
| --- | --- |
| Host agent | Reason about evidence, coordinate reviews and perform authorized feature work |
| Bundled CLI | Collect data, freeze context, validate signatures and responses, run approved fixed detectors and save artifacts |
| Maintainer | Select trusted policy, manage signer identities and approve knowledge/tool revisions |
| Reference packs | Supply Rust review questions and counterexamples, not repository policy |

Learning and review read immutable Git objects without executing target builds,
tests, hooks or generated detectors. Feature edits and checks require user
authorization. Commits, pushes and publication are separate operations.

The current fixed detector, `rust.forbidden-dependency.v1`, inspects Cargo
dependency declarations; it does not run Cargo or resolve the complete dependency
graph. Historical replay excludes today's reference packs and requires independent
human adjudication.

Missing context, expired knowledge, failed agents and omitted assessments remain
coverage gaps. Exact citations verify source identity, not the correctness of
model reasoning. Runtime or dependency changes require maintainer reapproval of
affected runtime-bound records.

## Documentation

| Topic | Reference |
| --- | --- |
| Installation and distribution | [Distribution guide](docs/distribution.md) |
| Project setup | [Environment and storage](.github/skills/repowise/references/setup.md) |
| Knowledge accumulation | [Synchronization](.github/skills/repowise/references/sync.md), [learning](.github/skills/repowise/references/learning.md) |
| Knowledge lifecycle | [Schema and revisions](.github/skills/repowise/references/knowledge.md), [approval](.github/skills/repowise/references/approval.md) |
| PR review | [Review workflow](.github/skills/repowise/references/review.md), [role coordination](.github/skills/repowise/references/review-agents.md) |
| Feature implementation | [Feature workflow](.github/skills/repowise/references/feature.md) |
| Evaluation | [Temporal replay](.github/skills/repowise/references/replay.md), [scope and limitations](docs/pilot.md) |
| Development | [Contributor instructions](AGENTS.md) |
