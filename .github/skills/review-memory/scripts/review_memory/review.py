"""Pinned, bounded read-only review and strict host-response validation."""
from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .common import (Error, canonical_bytes, digest, git, load_json, lock, resolve_commit, runtime_info,
                     safe_path, utcnow, write_json)
from .detectors import forbidden_dependency
from .render import save_report
from .repository_context import (_added_lines, _matches, _path, _patterns, _source_lines,
                                 capture_context)
from .review_contract import (OUTPUT_CONTRACT, REPOSITORY_REVIEW, SCHEMA_VERSION, _object, _text,
                             validate_comparisons, validate_repository_assessments)
from .storage import validate_memory_root

def _applies(rule, path):
    applicability = rule.get("applicability", {})
    return (_matches(path, _patterns(applicability.get("paths", ["**"]))) and
            not _matches(path, _patterns(applicability.get("exclusions", []))))


def _changed_files(root, base, head):
    output = git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-status",
                 "-z", "--find-renames", base, head, "--", binary=True)
    tokens = output.decode("utf-8", errors="strict").split("\0")
    records, index = [], 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        index += 1
        old_path = _path(tokens[index])
        index += 1
        path = old_path
        if status.startswith(("R", "C")):
            path = _path(tokens[index])
            index += 1
        records.append({"path": path, "old_path": old_path, "status": status})
    return records


def _execution(rule):
    value = rule.get("execution", "manual")
    return value.get("mode", "manual") if isinstance(value, dict) else value


def _identity(rule):
    return rule["id"], rule["revision"]


def _finding(rule, candidate, origin):
    result = dict(candidate, knowledge_id=rule["id"], revision=rule["revision"], origin=origin)
    result["finding_id"] = digest({
        "knowledge_id": rule["id"], "revision": rule["revision"],
        "evidence": result["evidence"],
    })
    return result


def _report(task, findings, gaps, assessments, model, repository_assessments, awaiting=False):
    deduplicated = {f["finding_id"]: f for f in findings}
    findings = sorted(deduplicated.values(), key=lambda f: f["finding_id"])
    cap = task["max_displayed_findings"]
    all_gaps = list(dict.fromkeys(gaps))
    return {
        "schema_version": SCHEMA_VERSION, "run_id": task["run_id"], "task_id": task["task_id"],
        "input_hash": task["input_hash"], "repository": task["repository"],
        "base_sha": task["base_sha"], "head_sha": task["head_sha"],
        "code_source": task["code_source"], "policy_source": task["policy_source"],
        "runs_path": task["runs_path"],
        "approved_snapshot_hash": task["approved_snapshot_hash"], "trusted_sha": task["trusted_sha"],
        "runtime": task["runtime"], "report_runtime": runtime_info(), "model": model,
        "cache_reuse": False, "costs": {
            "status": "partial", "amount": None, "tokens": None,
            "static": {"executions": len(task.get("detector_runs", [])),
                       "elapsed_seconds": sum(run["elapsed_seconds"] for run in task.get("detector_runs", [])),
                       "llm_tokens": 0},
            "semantic": {"tokens": None if task["knowledge"] else 0,
                         "elapsed_seconds": None if task["knowledge"] else 0},
        },
        "detector_runs": task.get("detector_runs", []),
        "reference_selection": task.get("reference_selection"),
        "pull_request": task.get("pull_request"),
        "external_references": [
            {"pack_id": pack["id"], "content_hash": pack["content_hash"], "sources": pack["sources"]}
            for pack in task.get("reference_packs", [])
        ],
        "status": "incomplete" if all_gaps or awaiting else "complete",
        "scope": "Bounded repository-first review of approved rules; project precedents are evidence, not policy.",
        "awaiting_response": awaiting, "findings": findings, "displayed_findings": findings[:cap],
        "total_findings": len(findings), "omitted_findings": max(0, len(findings) - cap),
        "coverage_gaps": all_gaps, "assessments": assessments,
        "repository_assessments": repository_assessments,
        "context_selection": task["context_selection"], "publishing": "unsupported",
    }


def _run_directory(root, run_id, runs_root=None):
    if runs_root is None:
        return safe_path(root, f".review/local/runs/{run_id}")
    candidate = Path(runs_root)
    try:
        relative = candidate.relative_to(root.resolve()) if candidate.is_absolute() else candidate
    except ValueError as exc:
        raise Error("Custom runs_root must remain inside external memory storage.") from exc
    parts = relative.parts
    if parts != (".review", "local", "runs") and not (
            len(parts) >= 5 and parts[:3] == (".review", "local", "evaluation") and parts[-1] == "runs"):
        raise Error("Custom runs_root must be production runs or an isolated evaluation runs directory.")
    return safe_path(safe_path(root, relative), run_id)


def prepare_review(target_root: Path, repository: str, base: str, head: str, trusted_ref: str,
                   max_files: int = 100, max_bytes: int = 500000, at: str | None = None,
                   *, memory_root: Path, runs_root: Path | None = None, reference_query: str | None = None,
                   reference_limit: int = 3, context_paths: list[str] | None = None,
                   pr_context: dict | None = None) -> dict:
    from .core import load_git_snapshot

    target_root = Path(target_root).resolve()
    memory_root = validate_memory_root(target_root, memory_root)
    if (type(max_files) is not int or type(max_bytes) is not int or max_files < 1 or max_bytes < 1):
        raise Error("max_files and max_bytes must be positive integers.")
    if not isinstance(repository, str) or not repository.strip():
        raise Error("Repository identity must be explicit.")
    references, reference_selection = [], None
    if reference_query is not None:
        if at is not None:
            raise Error("Current reference packs cannot enter temporal replay; historical reference availability is not established.")
        from .packs import select_packs
        selection = select_packs(reference_query, reference_limit)
        consumed_reference_bytes, budget_omitted = 0, 0
        for pack in selection["packs"]:
            size = len(canonical_bytes(pack))
            if consumed_reference_bytes + size > 64000:
                budget_omitted += 1
                continue
            consumed_reference_bytes += size
            references.append(pack)
        reference_selection = {key: value for key, value in selection.items() if key != "packs"}
        reference_selection.update(byte_limit=64000, consumed_bytes=consumed_reference_bytes,
                                   budget_omitted_count=budget_omitted, selected_count=len(references))
    base_sha, head_sha = resolve_commit(target_root, base), resolve_commit(target_root, head)
    if pr_context is not None and (
            not isinstance(pr_context, dict) or pr_context.get("repository") != repository
            or pr_context.get("base_sha") != base_sha or pr_context.get("head_sha") != head_sha
            or pr_context.get("authority") != "untrusted_change_description"):
        raise Error("PR description must match this repository and the pinned code revisions.")
    if pr_context is not None:
        for field in ("title", "body", "url", "metadata_hash"):
            if not isinstance(pr_context.get(field), str):
                raise Error("PR description context is missing a required text field.")
        if type(pr_context.get("number")) is not int or pr_context["number"] < 1:
            raise Error("PR description context requires a positive PR number.")
        if (not isinstance(pr_context.get("coverage_gaps"), list)
                or any(not isinstance(gap, str) for gap in pr_context["coverage_gaps"])):
            raise Error("PR description coverage gaps must be an array of strings.")
    snapshot = load_git_snapshot(memory_root, trusted_ref, at=at)
    config = snapshot["config"]
    if config.get("repository") != repository:
        raise Error("Repository identity differs from the trusted configuration.")
    if config.get("publish", False) or config.get("execute_project_commands", False):
        raise Error("Publishing and project execution are unsupported.")
    max_rules = config.get("max_rules_per_run", 50)
    max_display = config.get("max_findings_per_pr", config.get("max_displayed_findings", 50))
    if type(max_rules) is not int or max_rules < 1 or type(max_display) is not int or max_display < 0:
        raise Error("Invalid configured rule/finding limit.")
    gaps = [f"{gap.get('knowledge_id', 'snapshot')}: {gap.get('reason', 'unspecified coverage gap')}"
            if isinstance(gap, dict) else str(gap)
            for gap in snapshot.get("manifest", {}).get("coverage_gaps", [])]
    if pr_context is not None:
        gaps.extend(pr_context["coverage_gaps"])
    try:
        changed = _changed_files(target_root, base_sha, head_sha)
    except (UnicodeError, IndexError, Error) as exc:
        changed = []
        gaps.append(f"Changed paths unavailable: {exc}")
    contexts, context_gaps, consumed, selection = capture_context(
        target_root, base_sha, head_sha, changed, max_files, max_bytes, config, context_paths=context_paths)
    gaps.extend(context_gaps)
    approved = snapshot["knowledge"]
    if not approved:
        gaps.append("No eligible approved knowledge; no repository-policy coverage.")
    rules = approved[:max_rules]
    gaps.extend(f"Rule limit omitted approved knowledge {r['id']} revision {r['revision']}"
                for r in approved[max_rules:])
    detectors = {d["id"]: d for d in snapshot["detectors"]}
    semantic, static_findings, assessments, detector_runs = [], [], [], []
    for rule in rules:
        identity = {"knowledge_id": rule["id"], "revision": rule["revision"]}
        mode = _execution(rule)
        applicable = [c for c in contexts if c["status"] != "context" and _applies(rule, c["path"])]
        if mode in ("static", "hybrid", "detector") and any(
                c["status"] != "context" and c["path"].split("/")[-1] == "Cargo.toml" for c in contexts):
            applicable += [c for c in contexts if c["path"].split("/")[-1] == "Cargo.toml"
                           and _applies(rule, c["path"])]
        if not applicable:
            if gaps:
                gaps.append(f"{rule['id']}: applicability may be hidden by incomplete context.")
                status = "needs_context"
            else:
                status = "not_applicable"
            assessments.append(dict(identity, status=status, rationale="No eligible changed path in captured context."))
            continue
        if mode in ("static", "hybrid", "detector"):
            reference = rule.get("detector_ref")
            detector = detectors.get(reference) if isinstance(reference, str) else None
            if detector is None:
                gaps.append(f"{rule['id']}: detector is not separately approved or is missing.")
                assessments.append(dict(identity, status="needs_context", rationale="Approved detector unavailable."))
            else:
                # Excluded manifests can supply workspace resolution, but cannot produce findings.
                started = time.perf_counter()
                detected = forbidden_dependency(detector, contexts)
                execution = {
                    "detector_id": detector["id"], "revision": detector.get("revision", 1),
                    "tool_id": detector["tool_id"], "elapsed_seconds": time.perf_counter() - started,
                    "coverage_gaps": detected["coverage_gaps"], "finding_count": len(detected["findings"]),
                }
                detector_runs.append(execution)
                eligible = {c["path"] for c in contexts if _applies(rule, c["path"])}
                static_findings.extend(_finding(rule, dict(
                    f, verification="executed", detector_id=detector["id"],
                    detector_revision=detector.get("revision", 1), detector_version=detector["tool_id"],
                    execution_result_ref=f"task.json#/detector_runs/{len(detector_runs) - 1}",
                ), "static") for f in detected["findings"]
                                       if f["evidence"]["path"] in eligible)
                gaps.extend(f"{rule['id']}: {gap}" for gap in detected["coverage_gaps"])
                assessments.append(dict(identity, status="needs_context" if detected["coverage_gaps"] else "checked",
                                        rationale="Allowlisted detector inspected parsed commit-qualified manifests."))
        if mode in ("llm", "hybrid"):
            semantic.append(rule)
            context_request = rule.get("applicability", {}).get("context")
            if context_request:
                gaps.append(f"{rule['id']}: requested module/dependency context is not guaranteed complete: "
                            + json.dumps(context_request, ensure_ascii=True))
        elif mode not in ("static", "detector"):
            gaps.append(f"{rule['id']}: approved {mode} rule needs manual coverage.")
            assessments.append(dict(identity, status="needs_context", rationale="Manual review required."))
    if semantic:
        gaps.append("Dependency/module context outside the bounded captured files was not inspected.")
    runtime = runtime_info()
    run_id = digest({"repository": repository, "base": base_sha, "head": head_sha,
                     "snapshot": snapshot["hash"], "runtime": runtime, "config": config,
                     "nonce": uuid.uuid4().hex})[:32]
    directory = _run_directory(memory_root, run_id, runs_root)
    task = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "task_id": f"review-{run_id}", "repository": repository,
        "base_sha": base_sha, "head_sha": head_sha, "trusted_sha": snapshot["trusted_sha"],
        "code_source": "target_repository", "policy_source": "external_memory",
        "runs_path": directory.parent.relative_to(memory_root).as_posix(),
        "approved_snapshot_hash": snapshot["hash"], "runtime": runtime, "config_hash": digest(config),
        "model_provider": config.get("model_provider", "unknown"),
        "created_at": utcnow(), "effective_at": at, "knowledge": semantic, "code_context": contexts,
        "coverage_gaps": gaps, "output_contract": deepcopy(OUTPUT_CONTRACT),
        "repository_review": deepcopy(REPOSITORY_REVIEW), "context_selection": selection,
        "pull_request": deepcopy(pr_context),
        "max_displayed_findings": max_display,
        "detector_runs": detector_runs,
        "reference_packs": references, "reference_selection": reference_selection,
        "limits": {"max_files": max_files, "max_bytes": max_bytes, "consumed_bytes": consumed,
                   "max_rules": max_rules},
    }
    task["input_hash"] = digest(task)
    state = {"task": task, "static_findings": static_findings, "assessments": assessments,
             "selected_knowledge": rules}
    state["state_hash"] = digest(state)
    model = {"provider": "unknown", "model": "unknown", "prompt_version": "unknown"}
    report = _report(task, static_findings, gaps, assessments, model, [], awaiting=bool(semantic))
    with lock(memory_root):
        if directory.exists():
            raise Error("Run already exists; prepare a new run.")
        directory.mkdir(parents=True)
        write_json(safe_path(directory, "task.json"), task)
        write_json(safe_path(directory, "request.json"), state)
        save_report(directory, report)
        if not semantic:
            completion = {"response_hash": None, "report": report}
            completion["completion_hash"] = digest(completion)
            write_json(safe_path(directory, "completion.json"), completion)
    return {"run_id": run_id, "task_id": task["task_id"], "input_hash": task["input_hash"],
            "task_path": str(directory / "task.json"), "report_path": str(directory / "report.json"),
            "requires_response": bool(semantic), "task": task, "report": report}


def _requested(item, requested):
    if (not isinstance(item["knowledge_id"], str) or type(item["revision"]) is not int
            or item["revision"] < 1):
        raise Error("Malformed knowledge identity.")
    identity = (item["knowledge_id"], item["revision"])
    try:
        if identity not in requested:
            raise Error("Unknown or stale knowledge id/revision.")
    except TypeError as exc:
        raise Error("Malformed knowledge identity.") from exc
    return requested[identity]


def _validate_response(response, task):
    _object(response, ("schema_version", "task_id", "input_hash", "model", "repository_assessments",
                       "assessments", "findings"), "Response")
    if type(response["schema_version"]) is not int or response["schema_version"] != SCHEMA_VERSION:
        raise Error("Unsupported response schema version.")
    if response["task_id"] != task["task_id"] or response["input_hash"] != task["input_hash"]:
        raise Error("Response belongs to a stale or different task.")
    _object(response["model"], ("provider", "model", "prompt_version"), "Model metadata")
    for key, value in response["model"].items():
        _text(value, f"model.{key}")
    if task["knowledge"] and response["model"]["provider"] != task["model_provider"]:
        raise Error("Response model provider differs from the trusted configured provider.")
    if not isinstance(response["assessments"], list) or not isinstance(response["findings"], list):
        raise Error("Assessments and findings must be arrays.")
    requested = {_identity(rule): rule for rule in task["knowledge"]}
    contexts = {c["path"]: c for c in task["code_context"]}
    assessed = {}
    gaps = validate_repository_assessments(response["repository_assessments"], task)
    for assessment in response["assessments"]:
        _object(assessment, ("knowledge_id", "revision", "status", "rationale"), "Assessment")
        rule = _requested(assessment, requested)
        identity = _identity(rule)
        if identity in assessed:
            raise Error("Duplicate semantic assessment.")
        if assessment["status"] not in ("checked", "not_applicable", "needs_context"):
            raise Error("Invalid assessment status.")
        _text(assessment["rationale"], "Assessment rationale")
        assessed[identity] = assessment
        if assessment["status"] == "needs_context":
            gaps.append(f"{rule['id']}: {assessment['rationale']}")
    for identity in requested.keys() - assessed.keys():
        gaps.append(f"Missing semantic assessment: {identity[0]} revision {identity[1]}")
    findings = []
    for candidate in response["findings"]:
        _object(candidate, ("knowledge_id", "revision", "basis", "comparisons", "evidence", "applicability_rationale",
                            "counterexample_checks", "impact", "triggering_conditions", "suggestion",
                            "uncertainty", "verification"), "Finding")
        rule = _requested(candidate, requested)
        assessment = assessed.get(_identity(rule))
        if not assessment or assessment["status"] != "checked":
            raise Error("Findings require a checked assessment for that rule.")
        validate_comparisons(candidate, task)
        for name in ("applicability_rationale", "impact", "triggering_conditions", "suggestion", "uncertainty"):
            _text(candidate[name], name)
        if candidate["verification"] not in ("inspected", "not_run"):
            raise Error("Model verification may only be inspected or not_run; execution claims are forbidden.")
        checks = candidate["counterexample_checks"]
        if not isinstance(checks, list) or not checks:
            raise Error("Explicit counterexample checks are required.")
        for check in checks:
            _text(check, "Counterexample check")
        evidence = candidate["evidence"]
        _object(evidence, ("path", "line_start", "line_end", "text"), "Evidence")
        path = _path(evidence["path"])
        context = contexts.get(path)
        if context is None or context["after"] is None or not _applies(rule, path):
            raise Error("Evidence must reference available, applicable current code.")
        start, end = evidence["line_start"], evidence["line_end"]
        lines = _source_lines(context["after"])
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise Error("Evidence line range is invalid.")
        _text(evidence["text"], "Evidence text")
        if evidence["text"] != "\n".join(lines[start - 1:end]):
            raise Error("Evidence text does not match exact HEAD source lines.")
        changed = [n for n in context["changed_lines"] if start <= n <= end]
        placement = "inline" if changed else "summary"
        before = context["before"]
        evidence_pre_existing = (before is not None and
                                 "\n" + evidence["text"] + "\n" in "\n" + "\n".join(_source_lines(before)) + "\n")
        finding = dict(candidate, placement=placement, inline_line=changed[0] if changed else None,
                       novelty="unknown", evidence_pre_existing=evidence_pre_existing)
        findings.append(_finding(rule, finding, "semantic"))
    return findings, gaps


def finalize_review(root: Path, run_id: str, response_path: Path, *, runs_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise Error("Invalid run id.")
    directory = _run_directory(root, run_id, runs_root)
    response = load_json(Path(response_path))
    response_hash = digest(response)
    with lock(root):
        state = load_json(safe_path(directory, "request.json"))
        state_hash = state.pop("state_hash", None)
        if state_hash != digest(state):
            raise Error("Stored immutable review request was modified.")
        task = state["task"]
        if task.get("schema_version") != SCHEMA_VERSION:
            raise Error("Unsupported review task schema; prepare a new repository-first review task.")
        if task["runtime"] != runtime_info():
            raise Error("Runtime changed since preparation; reapprove changed tools and prepare a new run.")
        task_without_hash = {key: value for key, value in task.items() if key != "input_hash"}
        if task["input_hash"] != digest(task_without_hash) or task["run_id"] != run_id:
            raise Error("Stored task identity/hash mismatch.")
        if (task.get("code_source") != "target_repository" or task.get("policy_source") != "external_memory"
                or task.get("runs_path") != directory.parent.relative_to(root).as_posix()):
            raise Error("Stored task belongs to a different code/policy source or isolated runs directory.")
        if load_json(safe_path(directory, "task.json")) != task:
            raise Error("Host task packet was modified after preparation.")
        completion = safe_path(directory, "completion.json")
        if completion.exists():
            previous = load_json(completion)
            completion_hash = previous.pop("completion_hash", None)
            if completion_hash != digest(previous):
                raise Error("Stored completion was modified.")
            if previous["response_hash"] is None and not task["knowledge"]:
                _validate_response(response, task)
                acknowledgement = safe_path(directory, "response.json")
                if acknowledgement.exists():
                    if digest(load_json(acknowledgement)) != response_hash:
                        raise Error("Run is already completed with a different response; prepare a new run.")
                else:
                    write_json(acknowledgement, response)
                return previous["report"]
            if previous["response_hash"] != response_hash:
                raise Error("Run is already completed with a different response; prepare a new run.")
            return previous["report"]
        findings, gaps = _validate_response(response, task)
        report = _report(task, state["static_findings"] + findings, task["coverage_gaps"] + gaps,
                         state["assessments"] + response["assessments"], response["model"],
                         response["repository_assessments"])
        write_json(safe_path(directory, "response.json"), response)
        save_report(directory, report)
        completed = {"response_hash": response_hash, "report": report}
        completed["completion_hash"] = digest(completed)
        write_json(completion, completed)
        return report
