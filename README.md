# Review Memory

English | [Simplified Chinese](README.zh.md)

A skill that syncs your project's pull request reviews, extracts team lessons, and
builds an evidence-backed knowledge base over time.

## Install

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

Requires Node.js/npx and Python 3.11+. For GitHub synchronization, install GitHub CLI
and sign in if you have not already:

```powershell
gh auth login
```

On first use, the assistant prepares an isolated Python environment for the skill.
You do not need to install a separate Python backend or configure a model API key.

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

The assistant will:

1. Save the project binding and prepare its runtime.
2. Collect merged PR reviews and discussions in batches.
3. Read the evidence and extract scoped lessons with exceptions and source links.
4. Save the candidates and knowledge index, then report progress and remaining work.

The default batch size is 20 PRs. Large histories or session limits leave a saved
queue that a later invocation can resume. To limit the initial history:

```text
Use review-memory to learn from acme/my-project's PRs merged since 2026-01-01.
Use the current workspace as the local directory.
```

## Common requests

| Goal | What to say in the assistant's chat |
| --- | --- |
| Continue syncing and learning | Use review-memory to continue syncing this project and finish pending knowledge extraction. |
| Check progress | Use review-memory to show the remaining PRs and learning tasks for this project. |
| Browse accumulated knowledge | Use review-memory to summarize this project's knowledge and link to the supporting PRs. |
| Recheck historical feedback | Use review-memory to finish the pending queue, then fully refresh historical PR feedback. |
| Learn from one PR | Use review-memory to learn from acme/my-project PR #123 and save the review lessons. |
| Review changes | Use review-memory to review these changes using this project's approved rules. |

Synchronization runs when you invoke the skill; installation does not start a
background service. Knowledge candidates accumulate without approval, but using
them as formal review rules requires a maintainer to inspect and approve them.

## Where knowledge is stored

Everything is stored under `.review` in **your target project**:

| Path | Contents |
| --- | --- |
| `.review/config.yaml` | Project binding |
| `.review/local/state/sync.json` | Sync progress and queued PRs |
| `.review/local/proposals` | Extracted candidates and evidence associations |
| `.review/local/learning/index.json` | Accumulated knowledge index |

Updating or reinstalling the skill does not intentionally delete this project
data. `.review/local` is ignored by Git by default to keep raw review data private.

## Further reading

- [Installation, relocation, and distribution](docs/distribution.md)
- [Synchronization workflow](.github/skills/review-memory/references/sync.md)
- [Knowledge approval](.github/skills/review-memory/references/approval.md)
- [Code review](.github/skills/review-memory/references/review.md)
- [Sources and licensing](THIRD_PARTY.md)
