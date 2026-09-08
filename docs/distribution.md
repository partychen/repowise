# Installation, storage, and distribution

English | [Simplified Chinese](distribution.zh.md)

## Install and select a project

Install through the [skills CLI](https://github.com/vercel-labs/skills):

```powershell
npx skills add partychen/repowise --skill repowise -a github-copilot -g
```

`partychen/repowise` is the skill repository, not your learning target.
Choose another `-a` value for a different host. Keep `-g` for personal installation:
project-scoped installation itself adds Skill files to the project, even though
runtime commands keep all knowledge outside it.
The installer may copy or link resources, so the host resolves the actual loaded
skill directory rather than assuming a particular project path.

In your target project's assistant session:

```text
Use repowise to sync acme/my-project's PR reviews and accumulate project knowledge.
```

The host prepares isolated Python dependencies, binds the target, collects merged
PRs, extracts evidence-backed candidates, and updates the knowledge index.
Subsequent requests resume saved work. No separate repowise wheel or model API
key is required.

The installation command only installs files; it does not run sync or start a
daemon. Python 3.11+ with `venv`/`ensurepip` and an authenticated GitHub CLI are
prerequisites. Dependency setup is local-only and remains subject to host tool
permissions. Downloading the Skill and syncing GitHub still require network access.

## Local development

From the source root:

```powershell
npx skills add . --skill repowise -a github-copilot -g --copy
```

Inspect any existing same-named installation before replacing it. Developers can
use `python -m pip install -e .` for tests; the installed skill does not depend on
that editable package.

## Self-contained resources

```text
repowise\
  SKILL.md
  requirements.txt
  wheels\
    pyyaml-6.0.3-py3-none-any.whl
    LICENSE.PyYAML.txt
    provenance.json
  scripts\
    main.py
    repowise\
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

## Independent project data

The default storage root is `~\.repowise\projects\<name>-<path-hash>`.
Project commands return its absolute `storage_root` and private `local_path`.
Neither the target checkout nor the Skill installation holds project state.
The namespace uses the canonical local target path, with a separately validated
GitHub repository binding. Same directory names at different paths stay isolated.

Use the global CLI option `--data-home ABSOLUTE_PATH` before a command, or the
host environment variable `REPOWISE_HOME`, to select another external parent.
It must be outside the target and Skill/source. The dependency runtime cache is
separate from this durable knowledge directory.

| Data | Storage-root-relative path |
| --- | --- |
| Project binding | `.review/config.yaml` |
| Local target-path identity | `.review/project.json` |
| Resumable queue | `.review/local/state/sync.json` |
| Evidence and tasks | `.review/local/raw`, `.review/local/proposals/tasks` |
| Extracted candidates | `.review/local/proposals` |
| Unapproved knowledge index | `.review/local/learning/index.json` |
| Feature handoffs and host completion records | `.review/local/features` |
| Approved rules | `.review/knowledge`, `.review/approvals` |

Moving the skill does not require editing a configured source path; the host
resolves its new location and the independent data stays in place. Moving the
target to a different canonical path selects a different namespace. This
product has one external-storage layout, with no in-repo memory fallback.
Resolve task and evidence paths against `storage_root`,
not the target or Skill. Never rewrite historical signatures or task hashes.

Learning requires no Git repository in the memory directory. For approved-policy
review, the maintainer initializes that external storage root as its own Git
repository and commits the inspected policy/config/signer authorization there.
`--trusted-ref` names a commit in this separate repository; base/head stay in the
read-only code repository. `.review/local` and the local binding file are excluded
from policy Git history, without changing the target's ignore files.

The same installation also prepares feature context. The CLI remains read-only
toward the target; only the separately user-authorized host performs feature
edits and checks, then records its reported outcome with `feature-finish`.

`approval-queue` produces a readable candidate handoff and `prepare-approval`
prepares a selected unsigned request. These do not sign, grant trust or commit.
See the [approval workflow](../.github/skills/repowise/references/approval.md).
Never publish private local memory as part of a Skill release. Runtime changes
invalidate approval bindings and require maintainer reapproval rather than a bypass.

## Distribution

The repository is [partychen/repowise](https://github.com/partychen/repowise).
Keep `.github/skills/repowise` discoverable by the skills CLI. Installing from
the repository requires neither an npm package nor a PyPI release.

To produce optional release attachments:

```powershell
python .\tools\package_skill.py
# Optional standalone CLI distribution, not required for Skill installation:
python -m pip wheel . --no-deps --wheel-dir .\dist
```

The skill ZIP and checksum use the version from `pyproject.toml`. The ZIP contains
only explicitly listed resources, excluding virtual environments and private data.
Its entire `repowise` folder can also be installed manually in a host-supported
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
