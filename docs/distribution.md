# Installation, relocation, and distribution

English | [Simplified Chinese](distribution.zh.md)

## Install and select a project

Install through the [skills CLI](https://github.com/vercel-labs/skills):

```powershell
npx skills add partychen/review-memory --skill review-memory -a github-copilot -g
```

`partychen/review-memory` is the skill repository, not your learning target.
Choose another `-a` value for a different host; omit `-g` for project-scoped
installation. Prefer a trusted personal installation when reviewing untrusted PRs.
The installer may copy or link resources, so the host resolves the actual loaded
skill directory rather than assuming a particular project path.

In your target project's assistant session:

```text
Use review-memory to sync acme/my-project's PR reviews and accumulate project knowledge.
```

The host prepares isolated Python dependencies, binds the target, collects merged
PRs, extracts evidence-backed candidates, and updates the knowledge index.
Subsequent requests resume saved work. No separate review-memory wheel or model API
key is required.

The installation command only installs files; it does not run sync or start a
daemon. Python 3.11+ and an authenticated GitHub CLI are prerequisites. Dependency
setup and network access remain subject to the host's tool permissions.

## Local development

From the source root:

```powershell
npx skills add . --skill review-memory -a github-copilot -g --copy
```

Inspect any existing same-named installation before replacing it. Developers can
use `python -m pip install -e .` for tests; the installed skill does not depend on
that editable package.

## Self-contained resources

```text
review-memory\
  SKILL.md
  requirements.txt
  scripts\
    main.py
    review_memory\
  references\
  packs\
```

`main.py setup` creates a dedicated user-cache virtual environment with the pinned
dependency. Regular commands use the skill's bundled code, not a global package.
The host handles setup; users do not need to locate scripts or run pip manually.
Recreate the environment on a new machine rather than copying it. First-time setup
requires access to the dependency distribution.

## Project data and relocation

| Data | Target-relative path |
| --- | --- |
| Project binding | `.review/config.yaml` |
| Resumable queue | `.review/local/state/sync.json` |
| Evidence and tasks | `.review/local/raw`, `.review/local/proposals/tasks` |
| Extracted candidates | `.review/local/proposals` |
| Unapproved knowledge index | `.review/local/learning/index.json` |
| Approved rules | `.review/knowledge`, `.review/approvals` |

Moving the skill does not require editing a configured source path; the host
resolves its new location. Preserve the necessary `.review` data when moving the
target project. Sync state uses project-relative paths. Do not rewrite historical
signatures or task hashes. Never include private `.review/local` data in a release.
Formal approvals bind runtime code and dependency versions; runtime changes can
require a maintainer to reapprove rules.

## Distribution

The repository is [partychen/review-memory](https://github.com/partychen/review-memory).
Keep `.github/skills/review-memory` discoverable by the skills CLI. Installing from
the repository requires neither an npm package nor a PyPI release.

To produce optional release attachments:

```powershell
python .\tools\package_skill.py
# Optional standalone CLI distribution, not required for Skill installation:
python -m pip wheel . --no-deps --wheel-dir .\dist
```

The skill ZIP and checksum use the version from `pyproject.toml`. The ZIP contains
only explicitly listed resources, excluding virtual environments and private data.
Its entire `review-memory` folder can also be installed manually in a host-supported
skill directory.

The project owner must select the license and retain source notices before a
release. Packaging never creates a repository or uploads artifacts automatically.
Repository-based installation does not depend on release attachments.

## Sync scope

The default scope is all enumerable merged PRs, processed in batches of 20.
An initial `sync --since YYYY-MM-DD` limits the inclusive merge-date range; the
saved scope cannot silently change. Pagination safety limits and read failures
remain visible incomplete results.

Incremental detection compares PR list metadata. After the queue completes,
`sync --refresh` starts a full feedback reread to catch changes not reflected in
that metadata. Identical evidence does not generate duplicate tasks.
Collection and knowledge extraction have separate completion states. Scheduling
periodic invocations requires a host scheduler or an explicitly configured job.
