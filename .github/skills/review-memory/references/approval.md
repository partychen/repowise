# Manual signed approval

The agent may prepare a request and explain it. It must not sign, select private
keys, add trusted signers, or treat a natural-language confirmation as a signature.
The following signing and trust-configuration steps belong to the **maintainer**.

## 1. Independently inspect content

Start from a candidate, such as the synthetic `examples\knowledge.yaml`, and
replace every synthetic claim with actual repository evidence. Check scope,
exceptions, ownership, both kinds of examples, revision and source availability.

For approval, prepare a separate reviewed file with `maturity: approved`,
`owner` and `effective_from`. This text alone remains unauthorized.
Preserve the original candidate for audit.

Static/hybrid rules must refer to a **separately approved** detector. The pilot
accepts only `tool_id: rust.forbidden-dependency.v1`, with exactly
`from_package` and `to_package` parameters. Its independent examples and
`validation_ref` document what was validated, not generated executable code.

## 2. Establish signer trust manually

The maintainer controls `.review\allowed_signers` using OpenSSH allowed-signers
syntax, binding the intended identity to a verified public key. Restrict the
namespace to `review-memory-v1` where appropriate. Initialization leaves
this file without trusted keys.

Never trust a public key just because the PR supplies it. Commit authorized
signer changes only through the repository's trusted governance process.
Keep private keys outside the repository and outside the agent's workflow.

## 3. Prepare an exact canonical request

```powershell
python -I $Runner --root $Target approval-request --file .\reviewed-knowledge.yaml --kind knowledge --identity maintainer@example.com --reason 'Reviewed scope, examples, and source versions'
```

For a detector, independently run the command with `--kind detector` and its
reviewed configuration file. Approval requests are not approvals.

The command returns `request`, `content_hash`, `namespace`, `status`, and a
maintainer-signature note. **Sign the file at `request`, not the CLI's printed
result object.** That file is canonical JSON containing:

```text
schema_version, repository, kind, identity, reason, requested_at,
content_hash, content, runtime, signature_namespace
```

Canonical bytes are JSON with sorted keys, ASCII escapes, compact separators,
no NaN, and exactly one trailing LF, encoded as UTF-8. `content_hash` is SHA-256
over the same canonical encoding of `content`. `runtime` includes the trusted
implementation's version and hash. The request generator writes these bytes;
do not reconstruct them, edit timestamps, or reformat them with a text editor.
Runtime binding also includes the Python and PyYAML versions. Changing those
versions requires a new request and maintainer approval, not a verification bypass.

## 4. Maintainer signs outside the agent

After independently inspecting that exact request, the maintainer runs:

```powershell
ssh-keygen -Y sign -f <maintainer-key> -n review-memory-v1 <request>
```

`<maintainer-key>` and `<request>` are placeholders to replace, not literal
PowerShell syntax. OpenSSH writes the detached signature beside the request.
**This is a manual instruction, never an agent tool call.**

## 5. Verify and record

The verified import can be run after the maintainer supplies the signature:

```powershell
python -I $Runner --root $Target approve --request REQUEST_PATH --signature SIGNATURE_PATH
```

Verification checks identity authorization, namespace, signature, content,
repository and runtime. A modified existing revision is rejected; use a higher
revision and obtain a new signature. Do not bypass a failure by changing trust.

The maintainer then commits the approved knowledge/detector files, approval
records, config and signer authorization to the independently trusted target
branch. The Skill does not commit or publish them automatically.

```powershell
python -I $Runner --root $Target snapshot --trusted-ref POLICY_SHA
```

This renders derived policy views from the trusted Git commit. It does not
approve local candidates. Every runtime change requires re-approval because
signatures pin the full runtime hash. This strict behavior is a deliberate MVP
default, including for tool changes that appear unrelated to a particular rule.

## Retirement and historical meaning

Submit a higher signed knowledge revision with `needs_review`, `deprecated` or
`archived` when a rule should stop participating. Do not erase prior records to
rewrite history. Historical cutoffs consider recorded approval and source
availability as well as `effective_from`; dates cannot manufacture past approval.
Use the same `approval-request --kind knowledge` and manual signing workflow
for these states. All four signed states (`approved`, `needs_review`,
`deprecated`, `archived`) are supported; only eligible `approved` knowledge
participates in formal policy review.
