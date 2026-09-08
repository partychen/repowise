# Reference packs: questions before rules

These JSON packs contain contextual review questions, legitimate counterexamples,
required evidence and explicit non-rules. Their wording is independently authored;
upstream-informed checks carry pinned `source_ids`. All remain `authority: reference`,
not signed policy or detectors.

| Pack | Focus |
| --- | --- |
| `rust-ownership` | Ownership transfer, resource retention, cleanup |
| `rust-errors-api` | Failure behavior, panic boundaries, public contracts |
| `rust-concurrency-async` | Lock progress, cancellation, bounded work |
| `rust-type-boundaries` | Validated state, conversions, trait commitments |
| `rust-performance` | Work amplification, actual costs, measurement limits |
| `rust-unsafe-ffi` | Unsafe preconditions, foreign ownership, ABI contracts |
| `rust-domain-modeling` | Entity identity, invariants and domain boundaries |
| `rust-ecosystem` | Dependency compatibility, features and integration choices |
| `rust-design-reasoning` | Diagnose ownership/design assumptions without blanket anti-pattern rules |
| `rust-api-conventions` | Naming, public conventions and version-aware API choices |
| `rust-domain-cli` | Configuration, output streams and process behavior |
| `rust-domain-cloud-native` | Bounded work, retries and graceful shutdown |
| `rust-domain-embedded` | Interrupts, target resources and hardware ownership |
| `rust-domain-fintech` | Numeric precision, idempotency and auditability |
| `rust-domain-iot` | Offline operation, reconnects and constrained devices |
| `rust-domain-ml` | Shapes, device resources and reproducibility |
| `rust-domain-web` | Request boundaries, runtime progress and application state |

```powershell
python -I $Runner --root $Target packs list
python -I $Runner --root $Target packs select --query 'Rust mutex cancellation' --limit 2
python -I $Runner --root $Target packs select --query 'fintech money rounding' --limit 2
python -I $Runner --root $Target packs sources
```

Selection uses keyword matching, not embeddings or automatic architectural
understanding. Inspect relevance manually. Selecting a pack never approves it,
changes a repository snapshot, or authorizes tools.
Use English query terms; tokens are matched at token boundaries. Translate the
user's topic into English before querying if their request uses another language.
Specific topics outrank the generic Rust tag. Results disclose matched tags,
omitted matches and content hashes.

## Upstream coverage and provenance

The source inventory is `packs\sources\actionbook-rust-skills.json`, pinned to
`actionbook/rust-skills@5c40d3ad785193231b7d0dbfb8e1eb447e5edd94`.
It classifies all 38 `skills/*/SKILL.md` entrypoints: 23 knowledge entries map
to packs, two routing entries inform topic/domain selection, and 13 tool or
installation workflows remain excluded with explicit reasons.

Each upstream source records repository, commit, path, URL and transformation;
each attributed check names its source IDs. This is entrypoint-level synthesis,
not a claim to have copied all linked auxiliary documents or generated crate skills.
See [the packaged source inventory](../packs/sources/actionbook-rust-skills.json).

## How to use one

1. Read the question and applicability, not only its title.
2. Identify a relevant repository contract or a concrete behavior concern.
3. Seek the stated evidence and test the valid counterexamples.
4. If useful, propose a narrowly scoped repository knowledge item citing the
   pack as `external_reference` alongside actual repository evidence.
5. Obtain independent human approval before it participates in policy review.

Reference-only concerns may be useful learning candidates; do not mislabel them
as violations of an approved repository rule.

## Include references in a current review

```powershell
python -I $Runner --root $Target review --repository owner/repo --base BASE_SHA --head HEAD_SHA --trusted-ref POLICY_SHA --reference-query 'axum async cancellation' --reference-limit 3
```

Preparation freezes selected pack contents, source versions and content hashes
inside the task. The report retains the selected source list. Existing response
validation still accepts only approved knowledge IDs: a source or check ID is not
a policy ID. The reference budget is 64 KB, with omitted packs explicitly reported.
The selector accepts limits from 1 to 20; use a narrow query instead of all packs.
As in every current review, base/head belong to the target and `POLICY_SHA`
belongs to the project's independent external memory Git repository.

Temporal replay does not load current reference packs. Passing a reference query
with a historical cutoff is rejected until historical reference availability can
be independently established. Do not manually feed contemporary packs into replay.

## Version and authority limits

Official Rust/std/Tokio URLs are reading references. `latest` in a documentation
URL is navigation, not a requirement to upgrade dependencies. Select the
project's actual documented version and features when reasoning about behavior.
No linked page is an instruction to execute a command or install a skill.

The unsafe/FFI pack asks about local safety contracts. It is not an exploitation
workflow or a guarantee of exhaustive security analysis. The performance pack
does not authorize benchmarks or fabricated measurements.
