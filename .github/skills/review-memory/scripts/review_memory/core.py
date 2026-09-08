from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .common import (
    Error, atomic_write, canonical_bytes, digest, git, load_json, load_yaml,
    lock, parse_yaml, resolve_commit, runtime_info, safe_path, timestamp,
    utcnow, write_json, write_yaml,
)

DEFAULT_CONFIG = {
    "schema_version": 1,
    "repository": None,
    "max_findings_per_pr": 10,
    "max_rules_per_run": 50,
    "publish": False,
    "execute_project_commands": False,
    "model_provider": "host",
    "raw_retention_days": 30,
}
KNOWLEDGE_STATES = {"candidate", "lesson", "approved", "needs_review", "deprecated", "archived"}
NAMESPACE = "review-memory-v1"


def _object(value, label):
    if not isinstance(value, dict):
        raise Error(f"{label} must be an object.")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise Error(f"{label} must be nonempty text.")


def _list(value, label):
    if not isinstance(value, list):
        raise Error(f"{label} must be a list.")


def validate_repository(repository: str):
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise Error("repository must be owner/name.")


def validate_config(config: dict):
    _object(config, "config")
    if config.get("schema_version") != 1:
        raise Error("Unsupported config schema.")
    validate_repository(config.get("repository"))
    for field in ("max_findings_per_pr", "max_rules_per_run", "raw_retention_days"):
        if type(config.get(field)) is not int or config[field] <= 0:
            raise Error(f"{field} must be a positive integer.")
    if config.get("publish") is not False or config.get("execute_project_commands") is not False:
        raise Error("The bundled CLI cannot publish or execute target commands; these flags must remain false.")
    _text(config.get("model_provider"), "model_provider")


def initialize(root: Path, repository: str) -> dict:
    validate_repository(repository)
    repository = repository.lower()
    root = root.resolve()
    config_path = safe_path(root, ".review/config.yaml")
    with lock(root):
        if config_path.exists():
            config = load_yaml(config_path)
            validate_config(config)
            if config["repository"] != repository:
                raise Error("Already initialized for a different repository.")
            return {"status": "already_initialized", "repository": repository}
        for directory in ("knowledge", "detectors", "approvals", "local/raw", "local/cache",
                          "local/proposals", "local/runs", "local/evaluation", "local/features"):
            safe_path(root, f".review/{directory}").mkdir(parents=True, exist_ok=True)
        config = dict(DEFAULT_CONFIG, repository=repository)
        write_yaml(config_path, config)
        atomic_write(safe_path(root, ".review/.gitignore"), b"local/\nproject.json\n")
        signers = safe_path(root, ".review/allowed_signers")
        if not signers.exists():
            atomic_write(signers, b"# Maintainer-managed OpenSSH allowed_signers; no keys are trusted by default.\n")
        write_json(safe_path(root, ".review/index.json"), {"schema_version": 1, "knowledge": [], "snapshot_hash": None})
        atomic_write(safe_path(root, ".review/memory.md"),
                     b"# Review Memory\n\nNo approved knowledge. Reference packs are not repository policy.\n")
    return {"status": "initialized", "repository": repository, "approval": "SSH signature required; no trusted signers configured"}


def validate_knowledge(item: dict, *, approving=False):
    _object(item, "knowledge")
    if item.get("schema_version") != 1 or not re.fullmatch(r"K-[A-Za-z0-9-]+", str(item.get("id", ""))):
        raise Error("Knowledge requires schema_version 1 and K-* id.")
    if type(item.get("revision")) is not int or item["revision"] < 1:
        raise Error("Knowledge revision must be a positive integer.")
    for field in ("title", "principle"):
        _text(item.get(field), field)
    if item.get("maturity") not in KNOWLEDGE_STATES:
        raise Error("Invalid knowledge maturity.")
    if item.get("execution") not in {"manual", "llm", "static", "hybrid"}:
        raise Error("Invalid execution mode.")
    if item.get("enforcement") != "advisory":
        raise Error("Only advisory knowledge is supported.")
    applicability = item.get("applicability")
    _object(applicability, "applicability")
    for field in ("paths", "exclusions"):
        _list(applicability.get(field), f"applicability.{field}")
        for pattern in applicability[field]:
            _text(pattern, f"applicability.{field}[]")
            safe_path(Path.cwd(), pattern)
    if not applicability["paths"]:
        raise Error("At least one applicability path is required.")
    _text(applicability.get("context"), "applicability.context")
    _list(item.get("sources"), "sources")
    for source in item["sources"]:
        _object(source, "source")
        for field in ("kind", "reference", "version", "available_at"):
            _text(source.get(field), f"source.{field}")
        if source["kind"] not in {"history", "policy", "external_reference"}:
            raise Error("Invalid source kind.")
        timestamp(source["available_at"])
    examples = item.get("examples")
    _object(examples, "examples")
    for field in ("valid", "violating"):
        _list(examples.get(field), f"examples.{field}")
        for example in examples[field]:
            _text(example, f"examples.{field}[]")
    _list(item.get("exceptions"), "exceptions")
    for exception in item["exceptions"]:
        _text(exception, "exception")
    for field in ("observed_at", "effective_from", "valid_until"):
        if item.get(field) is not None:
            timestamp(item[field])
    if item.get("execution") in {"static", "hybrid"} and not re.fullmatch(r"D-[A-Za-z0-9-]+", str(item.get("detector_ref", ""))):
        raise Error("Static/hybrid knowledge must reference a separately approved detector ID.")
    if approving:
        if item["maturity"] not in {"approved", "needs_review", "deprecated", "archived"}:
            raise Error("Signed revisions must approve, suspend, deprecate, or archive knowledge.")
        _text(item.get("owner"), "owner")
        _text(item.get("effective_from"), "effective_from")
        if not item["sources"] or not examples["valid"] or not examples["violating"]:
            raise Error("Approval requires versioned sources and both valid/violating examples.")
        if item.get("valid_until") and timestamp(item["valid_until"]) <= timestamp(item["effective_from"]):
            raise Error("valid_until must be later than effective_from.")
    return item


def validate_detector(item: dict):
    _object(item, "detector")
    if item.get("schema_version") != 1 or not re.fullmatch(r"D-[A-Za-z0-9-]+", str(item.get("id", ""))):
        raise Error("Detector requires schema_version 1 and D-* id.")
    if type(item.get("revision")) is not int or item["revision"] < 1:
        raise Error("Detector revision must be positive.")
    if item.get("tool_id") != "rust.forbidden-dependency.v1":
        raise Error("Unknown detector tool ID. Generated implementations cannot be executed.")
    parameters = item.get("parameters")
    _object(parameters, "parameters")
    if set(parameters) != {"from_package", "to_package"}:
        raise Error("Detector parameters must be from_package and to_package.")
    for value in parameters.values():
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise Error("Invalid Cargo package name.")
    _text(item.get("owner"), "owner")
    _text(item.get("validation_ref"), "validation_ref")
    for field in ("valid_examples", "violating_examples"):
        if not isinstance(item.get(field), list) or not item[field]:
            raise Error("Detector approval requires independent valid and violating examples.")
    return item


def _content(kind: str, content):
    if kind == "knowledge":
        return validate_knowledge(content, approving=True)
    if kind == "detector":
        return validate_detector(content)
    raise Error("Approval kind must be knowledge or detector.")


def approval_request(root: Path, content_path: Path, kind: str, identity: str, reason: str) -> dict:
    _text(identity, "identity")
    _text(reason, "reason")
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]+", identity):
        raise Error("Invalid signer identity.")
    config = load_yaml(safe_path(root, ".review/config.yaml"))
    validate_config(config)
    content = _content(kind, load_yaml(content_path))
    payload = {
        "schema_version": 1, "repository": config["repository"], "kind": kind,
        "identity": identity, "reason": reason, "requested_at": utcnow(),
        "content_hash": digest(content), "content": content,
        "runtime": runtime_info(),
        "signature_namespace": NAMESPACE,
    }
    key = digest(payload)
    path = safe_path(root, f".review/local/proposals/approval-{key}.json")
    atomic_write(path, canonical_bytes(payload))
    return {"request": str(path), "content_hash": payload["content_hash"],
            "namespace": NAMESPACE, "status": "awaiting_maintainer_signature",
            "note": "A maintainer must independently inspect and sign this exact file. The skill must not sign it."}


def verify_signature(payload: dict, signature: str, signers: str, *, root: Path):
    _object(payload, "approval payload")
    if payload.get("schema_version") != 1:
        raise Error("Unknown approval schema.")
    identity = payload.get("identity")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.@+-]+", identity):
        raise Error("Invalid approval identity.")
    _text(payload.get("reason"), "approval reason")
    timestamp(payload.get("requested_at"))
    if payload.get("content_hash") != digest(payload.get("content")):
        raise Error("Approval content hash mismatch.")
    _content(payload.get("kind"), payload.get("content"))
    namespace = payload.get("signature_namespace")
    if namespace != NAMESPACE:
        raise Error("Unknown approval signature namespace.")
    cache = safe_path(root, ".review/local/cache")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="review_memory-verify-", dir=cache) as directory:
        directory = Path(directory)
        signature_file, signers_file = directory / "signature", directory / "allowed_signers"
        signature_file.write_text(signature, encoding="utf-8")
        signers_file.write_text(signers, encoding="utf-8")
        try:
            result = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(signers_file), "-I", identity,
                 "-n", namespace, "-s", str(signature_file)],
                input=canonical_bytes(payload), capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Error("OpenSSH ssh-keygen with SSH signature support is required for approval verification.") from exc
        if result.returncode:
            raise Error("Invalid or unauthorized approval signature.")


def approve(root: Path, request_path: Path, signature_path: Path) -> dict:
    payload = load_json(request_path)
    _object(payload, "approval payload")
    config = load_yaml(safe_path(root, ".review/config.yaml"))
    validate_config(config)
    if payload.get("repository") != config["repository"]:
        raise Error("Approval is for a different repository.")
    signature = signature_path.read_text(encoding="utf-8")
    signers = safe_path(root, ".review/allowed_signers").read_text(encoding="utf-8")
    verify_signature(payload, signature, signers, root=root)
    if payload.get("runtime") != runtime_info():
        raise Error("Runtime changed since the approval request; create a new request for maintainer review.")
    content = payload["content"]
    group = "knowledge" if payload["kind"] == "knowledge" else "detectors"
    key = f"{content['id']}-r{content['revision']}"
    target = safe_path(root, f".review/{group}/{key}.yaml")
    record_path = safe_path(root, f".review/approvals/{key}.json")
    with lock(root):
        if target.exists() or record_path.exists():
            if not target.exists() or not record_path.exists():
                raise Error("Partial approval exists; inspect it before retrying.")
            existing = load_json(record_path)
            if load_yaml(target) != content or existing["payload"] != payload or existing["signature"] != signature:
                raise Error("This revision already exists with different content or approval. Create a new revision.")
            return {"status": "already_approved", "id": content["id"], "revision": content["revision"]}
        for previous in safe_path(root, f".review/{group}").glob(f"{content['id']}-r*.yaml"):
            if load_yaml(previous)["revision"] >= content["revision"]:
                raise Error("Revision must increase monotonically.")
        record = {"payload": payload, "signature": signature, "recorded_at": utcnow()}
        write_json(record_path, record)
        write_yaml(target, content)
    return {"status": "approved", "id": content["id"], "revision": content["revision"],
            "note": "A maintainer must commit approved files, config, and signer authorization in the "
                    "external memory/policy repository before review, not in the target branch."}


def resolve_policy_commit(root: Path, trusted_ref: str) -> str:
    root = Path(root).resolve()
    guidance = ("A maintainer must initialize the external memory's own Git repository, commit "
                "its inspected policy/config/signer authorization, and select a trusted policy commit.")
    try:
        toplevel = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    except Error as exc:
        raise Error(f"External memory has no policy Git history. {guidance}") from exc
    if toplevel != root:
        raise Error(f"External memory root must be its own Git toplevel, not an ancestor's checkout. {guidance}")
    common_directory = Path(git(root, "rev-parse", "--git-common-dir"))
    if not common_directory.is_absolute():
        common_directory = root / common_directory
    if not common_directory.resolve().is_relative_to(root):
        raise Error(f"External memory must own its Git metadata; linked worktrees or shared external "
                    f"Git directories are not independent policy repositories. {guidance}")
    try:
        return resolve_commit(root, trusted_ref)
    except Error as exc:
        raise Error(f"Trusted policy commit is unavailable in external memory. {guidance}") from exc


def load_git_snapshot(root: Path, trusted_ref: str, at: str | None = None) -> dict:
    root = Path(root).resolve()
    sha = resolve_policy_commit(root, trusted_ref)
    cutoff = timestamp(at) if at else timestamp(utcnow())
    try:
        configuration = git(root, "show", f"{sha}:.review/config.yaml")
        signers = git(root, "show", f"{sha}:.review/allowed_signers")
    except Error as exc:
        raise Error("Trusted external policy history is incomplete. A maintainer must commit "
                    ".review/config.yaml and .review/allowed_signers in the external memory repository; "
                    "target policy is never a fallback.") from exc
    config = parse_yaml(configuration)
    validate_config(config)
    paths = git(root, "ls-tree", "-r", "--name-only", sha, "--", ".review/approvals").splitlines()
    records = []
    for path in sorted(paths):
        if not path.endswith(".json"):
            continue
        safe_path(root, path)
        record = json.loads(git(root, "show", f"{sha}:{path}"))
        _object(record, "approval record")
        if not {"payload", "signature", "recorded_at"} <= record.keys() or not isinstance(record["signature"], str):
            raise Error("Malformed approval record.")
        payload = record["payload"]
        verify_signature(payload, record["signature"], signers, root=root)
        if payload.get("repository") != config["repository"]:
            raise Error("Cross-repository approval in trusted snapshot.")
        content = payload["content"]
        key = f"{content['id']}-r{content['revision']}"
        group = "knowledge" if payload["kind"] == "knowledge" else "detectors"
        stored = parse_yaml(git(root, "show", f"{sha}:.review/{group}/{key}.yaml"))
        if stored != content:
            raise Error(f"Approved content was changed without a new signature: {key}")
        available = max(timestamp(payload["requested_at"]), timestamp(record["recorded_at"]))
        if payload["kind"] == "knowledge":
            available = max(available, timestamp(content["effective_from"]),
                            *(timestamp(source["available_at"]) for source in content["sources"]))
        if available <= cutoff:
            records.append(record)
    latest = {}
    for record in records:
        item = record["payload"]["content"]
        if item["id"] not in latest or latest[item["id"]]["payload"]["content"]["revision"] < item["revision"]:
            latest[item["id"]] = record
    knowledge, detectors = [], []
    gaps = []
    for record in sorted(latest.values(), key=lambda r: r["payload"]["content"]["id"]):
        item = record["payload"]["content"]
        active = (record["payload"]["kind"] == "detector" or
                  (item["maturity"] == "approved" and
                   (not item.get("valid_until") or timestamp(item["valid_until"]) > cutoff)))
        if active and record["payload"].get("runtime") != runtime_info():
            raise Error("Installed runtime differs from approved runtime. Reapprove after reviewing tool changes.")
        if record["payload"]["kind"] == "detector":
            detectors.append(item)
        elif item.get("valid_until") and timestamp(item["valid_until"]) <= cutoff:
            gaps.append({"knowledge_id": item["id"], "reason": "Knowledge expired; maintainer re-review required."})
        elif item["maturity"] == "approved":
            knowledge.append(item)
        elif item["maturity"] == "needs_review":
            gaps.append({"knowledge_id": item["id"], "reason": "Knowledge suspended pending maintainer review."})
    manifest = {
        "schema_version": 1, "trusted_sha": sha, "repository": config["repository"],
        "policy_source": "external_memory",
        "runtime": runtime_info(), "config": config,
        "approvals": [{"id": r["payload"]["content"]["id"], "revision": r["payload"]["content"]["revision"],
                       "hash": digest(r)} for r in sorted(latest.values(), key=lambda r: r["payload"]["content"]["id"])],
        "coverage_gaps": gaps,
    }
    return {"hash": digest(manifest), "knowledge": knowledge, "detectors": detectors,
            "config": config, "manifest": manifest, "trusted_sha": sha}


def render_snapshot(root: Path, trusted_ref: str) -> dict:
    snapshot = load_git_snapshot(root, trusted_ref)
    with lock(root):
        write_json(safe_path(root, ".review/index.json"), snapshot)
        lines = ["# Review Memory", "", f"Approved snapshot: `{snapshot['hash']}`", "",
                 "Advisory review only; not a proof of program correctness.", ""]
        for item in snapshot["knowledge"]:
            lines.extend([f"## {item['id']} r{item['revision']}: {item['title']}", "",
                          item["principle"], "", f"Scope: {item['applicability']['context']}", ""])
        if not snapshot["knowledge"]:
            lines.extend(["No eligible approved knowledge.", ""])
        atomic_write(safe_path(root, ".review/memory.md"), "\n".join(lines).encode("utf-8"))
    return {"snapshot_hash": snapshot["hash"], "knowledge_count": len(snapshot["knowledge"])}
