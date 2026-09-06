# Knowledge, provenance and scope

## Knowledge revision

`schema_version: 1` knowledge has:

| Field | Meaning |
| --- | --- |
| `id`, `revision` | Stable `K-*` identity and monotonically increasing positive revision |
| `title`, `principle` | A narrow, testable proposition, not a slogan |
| `maturity` | `candidate`, `lesson`, `approved`, `needs_review`, `deprecated`, `archived` |
| `execution` | `manual`, `llm`, `static`, `hybrid` |
| `enforcement` | Always `advisory` in this pilot |
| `applicability` | `paths`, `exclusions`, and explanatory `context` |
| `sources` | Versioned evidence with `kind`, `reference`, `version`, `available_at` |
| `examples` | Nonempty `valid` and `violating` examples required for approval |
| `exceptions` | Explicit legitimate alternatives and excluded circumstances |
| `owner` | Accountable maintainer; required for approval |
| `observed_at` | Optional observation time |
| `effective_from`, `valid_until` | Activation time and optional expiry, RFC 3339 with timezone |
| `detector_ref` | `D-*` reference required for `static` / `hybrid` |

Quote timestamps in YAML so they remain JSON-compatible strings.
Approval timestamps must be quoted RFC 3339 strings with a timezone.
YAML anchors, aliases, custom tags and duplicate mapping keys are rejected;
use plain JSON-compatible data, not YAML object-construction features.
Use portable repository-relative glob paths such as `crates/api/**`; Windows
shell commands still use backslash filesystem paths.

An illustrative candidate shape (not a learned rule; replace its content and
evidence ID with findings supported by the actual task):

```json
{
  "schema_version": 1,
  "id": "K-request-boundary-context",
  "revision": 1,
  "title": "Preserve request context at public error boundaries",
  "principle": "At the service request boundary, retain the request context needed to diagnose a failed operation.",
  "maturity": "candidate",
  "execution": "llm",
  "enforcement": "advisory",
  "applicability": {
    "paths": ["src/handlers/**"],
    "exclusions": ["src/generated/**"],
    "context": "Only handlers whose documented diagnostics contract requires request context."
  },
  "sources": [],
  "evidence_ids": ["COPY_AN_ACTUAL_TASK_EVIDENCE_ID"],
  "examples": {
    "valid": ["A private typed helper propagates its error to a boundary that supplies context."],
    "violating": ["A boundary discards the operation context required by its diagnostics contract."]
  },
  "exceptions": ["Do not expose confidential request data in client-visible errors."],
  "owner": null,
  "effective_from": null,
  "valid_until": null,
  "detector_ref": null
}
```

Insert supported candidate objects into the host response's `candidates` array.
This example is synthetic, not permission to invent repository contracts or reuse
its principle without evidence. The CLI derives historical sources from cited bundles.

## Three source kinds, three different claims

- **`policy`**: an identified repository policy document or decision at an
  immutable revision. Cite its scope and any superseding decision.
- **`history`**: an observed review/comment/change at a recorded version.
  Keep disagreement, exceptions and uncertainty. One comment is evidence of a
  comment, not proof of the author's authority or the team's permanent rule.
- **`external_reference`**: reading material, including these Rust packs.
  Record what it explains; it does not establish what this repository requires.

`available_at` means when that source version was available, not merely when the
underlying PR was created. Edited comments must retain version-specific timing.
Do not backdate new knowledge to make a historical replay look better.

## From observation to an actionable rule

Bad: “Never clone”, “Mutex is wrong in async”, “Always use the latest Rust”.

Better: “For this service's bounded in-memory dispatch path, preserve the
documented cancellation behavior when ownership of a pending request moves.”
Then identify the concrete contract, paths, exceptions, positive and negative
examples, and what evidence would disprove the claimed violation.

Before proposing a rule, ask:

1. What behavior or repository contract is at risk?
2. Is the observation contextual, disputed, obsolete, or repeated independently?
3. What valid counterexample prevents overgeneralization?
4. Can a reviewer cite an actual changed line and explain its consequence?
5. Who owns this decision, and when should it be reconsidered?

## State and execution are separate

Changing `maturity` text to `approved` does not authorize anything. Only a valid
maintainer-signed revision included in a trusted snapshot can activate.
Newer signed `needs_review`, `deprecated` or `archived` revisions suspend use of
that knowledge; old signed records remain auditable. Expiry produces a coverage
gap and requires maintainer re-review, not silent extension.

`manual` is human-only coverage. `llm` requests host reasoning. `static` and
`hybrid` require a separately approved fixed detector configuration. Candidate
code is never loaded, imported, compiled or run.

See [approval](approval.md) and the synthetic `examples\knowledge.yaml`.
