# Working on review-memory

This project builds one Copilot skill, not a collection of separately triggered Rust skills.
The skill lives in `.github/skills/review-memory`; invoke its bundled `scripts/main.py`
with `python -I`, resolving the actual Skill installation directory. Python 3.11 or
newer is required. Users install with `npx skills add`; a separate runtime wheel is optional.

## Boundaries

- Reference packs provide questions and counterexamples, not approved repository policy.
- Model outputs may propose knowledge and findings, never grant permissions or approve themselves.
- Knowledge and detector approvals are separate, signed operations. Never create a production
  approval signature or change trusted signers on behalf of the skill.
- Review reads immutable Git objects. Do not checkout a PR or execute its build scripts,
  Cargo metadata, hooks, macros, tests, or newly generated detectors.
- Only explicitly registered, approved detector tool IDs may run.
- API failures, missing versions, omitted context, expired knowledge, and missing model
  assessments must remain visible coverage gaps, not successful checks.
- Keep raw data, model contexts, proposals, and evaluation answers under `.review/local`.
- Do not introduce network publishing or provider calls without a separate permission design.

## Development

Install the declared dependencies with `python -m pip install -e .`.
The bundled launcher `setup` prepares user dependencies, and `tools/package_skill.py`
builds Skill release archives. Editable installations are development-only.
Run focused regression tests with `python -m unittest tests.test_core -v`, or the full suite
with `python -m unittest discover -s tests -v`.
Set `REVIEW_MEMORY_TEST_INSTALL=1` when running `tests.test_installed_workflow` to
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
