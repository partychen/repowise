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
daemon. Python 3.11+ with `venv`/`ensurepip` and an authenticated GitHub CLI are
prerequisites. Dependency setup is local-only and remains subject to host tool
permissions. Downloading the Skill and syncing GitHub still require network access.

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
  wheels\
    pyyaml-6.0.3-py3-none-any.whl
    LICENSE.PyYAML.txt
    provenance.json
  scripts\
    main.py
    review_memory\
  references\
  packs\
```

`main.py setup` creates a dedicated user-cache virtual environment and installs the
bundled wheel **without accessing PyPI**. Its exact version and SHA256 are pinned
in `requirements.txt`; both the launcher and pip enforce the hash. Installation
uses no package index, global pip cache, dependency resolution, or source build.
The pure-Python wheel needs no native compiler or platform-specific binary.
It is built from pinned upstream PyYAML source, not presented as an unmodified
official PyPI wheel; `wheels/provenance.json` records the source and build details,
and the MIT license is retained.

Regular commands use the skill's bundled code, not a global package. The host
handles setup; users do not need to locate scripts or run pip manually. Recreate
the environment on a new machine rather than copying it. A missing or modified
wheel fails closed and requires a complete trusted Skill installation; setup
never silently switches to an online source.

If an older installation failed downloading from `files.pythonhosted.org`, update
the Skill at its actual loaded location and rerun setup. The new bundle gets a
different cache key, leaving the old incomplete environment unused. For interrupted
offline setup, follow the launcher's exact-path recovery instructions only after
confirming no setup process is active. Do not erase project knowledge or weaken TLS.

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

Repository/npx installs, source distributions, and Skill ZIPs must all include the
wheel, its license and provenance, and the matching hash-pinned requirements file.
To update the dependency, follow the recorded upstream/build recipe and review
the resulting wheel before updating its hash and provenance together. Dependency
or runtime updates require new maintainer approval where existing runtime bindings
no longer match; never rewrite old signatures to accommodate an upgrade.

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
