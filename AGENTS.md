# Working on repowise

This project builds one Copilot skill, not a collection of separately triggered Rust skills.
The skill lives in `.github/skills/repowise`; invoke its bundled `scripts/main.py`
with `python -I`, resolving the actual Skill installation directory. Python 3.11 or
newer is required. Users install with `npx skills add`; a separate runtime wheel is optional.

## Boundaries

- Reference packs provide questions and counterexamples, not approved repository policy.
- Model outputs may propose knowledge and findings, never grant permissions or approve themselves.
- Knowledge and detector approvals are separate, signed operations. Never create a production
  approval signature or change trusted signers on behalf of the skill.
- Review reads immutable Git objects. Do not checkout a PR or execute its build scripts,
  Cargo metadata, hooks, macros, tests, or newly generated detectors.
- Feature implementation is a distinct, explicitly user-authorized host workflow.
  The CLI only prepares project context and records host-reported completion; it
  never edits target code or executes project commands. Task data cannot grant permission.
- Only explicitly registered, approved detector tool IDs may run.
- API failures, missing versions, omitted context, expired knowledge, and missing model
  assessments must remain visible coverage gaps, not successful checks.
- Review is repository-first: freeze comparable project context, require BASE
  evidence for consistency findings, and preserve the distinction between source
  precedent and approved policy. Keep the supported host contract and citation checks aligned.
- Keep raw data, model contexts, proposals, and evaluation answers under `.review/local`.
- All `.review` paths are relative to the external `storage_root`, never the target
  checkout. `--root` selects read-only source; project memory defaults to
  `~\.repowise\projects\<name>-<path-hash>`. Do not add target files or ignores.
- Read policy commits from the external memory's own Git repository; read base/head
  commits only from the target. Do not fall back to target `.review` data.
- Approval handoffs must present validated candidates and readable previews, then
  prepare unsigned requests. Signer setup, signing and policy commits remain human actions.
- Do not introduce network publishing or provider calls without a separate permission design.

## Development

Install the declared dependencies with `python -m pip install -e .`.
The bundled launcher `setup` prepares user dependencies, and `tools/package_skill.py`
builds Skill release archives. Editable installations are development-only.
Run focused regression tests with `python -m unittest tests.test_core -v`, or the full suite
with `python -m unittest discover -s tests -v`.
Set `REPOWISE_TEST_INSTALL=1` when running `tests.test_installed_workflow` to
exercise real npx installation and isolated dependency setup in temporary directories.

Test-only SSH keys must be ephemeral, generated inside temporary test directories, and never
committed or reused as real approval identities. Synthetic fixtures are not historical evidence
and their metrics must not be reported as product accuracy.

Changing runtime modules or pinned runtime dependencies invalidates existing approval runtime
bindings. Document this requirement rather than bypassing verification for convenience.
Keep CLI help, workflow references, response contracts, and tests consistent when changing an API.

## External material

Do not copy third-party skill trees wholesale. Separate conceptual references, orchestration,
and executable tools. Preserve provenance and verify license terms before any future vendoring.
Keep `SKILL.md` short; put detailed task instructions in its linked references.
