"""Frozen host-only role planning and evidence-preserving consolidation."""
from __future__ import annotations

import re
from copy import deepcopy

from .common import Error, digest
from .review_contract import OUTPUT_CONTRACT, REPOSITORY_DIMENSIONS, _object, _text

VERSION = 1
DIMENSIONS = dict(REPOSITORY_DIMENSIONS)
ROLES = {
    "architecture": {"dimensions": ["architecture", "reuse_frameworks", "idioms"],
                     "focus": "Project architecture, existing mechanisms and material convention deviations."},
    "logic": {"dimensions": ["contracts_types", "change_scope"],
              "focus": "Business behavior, contracts, types, callers and intended change scope."},
    "security": {"dimensions": ["security"],
                 "focus": "Authentication, authorization, untrusted input and trust boundaries."},
    "reliability": {"dimensions": ["errors_lifecycle"],
                    "focus": "Errors, cancellation, retries, resource ownership and lifecycle."},
    "concurrency": {"dimensions": ["concurrency_performance"],
                    "focus": "Concurrency, blocking, contention and bounded performance costs."},
    "tests": {"dimensions": ["tests_observability"],
              "focus": "Behavioral regression coverage, logging and observability."},
}


def validate_options(mode, roles, max_workers):
    if not isinstance(mode, str) or mode not in ("auto", "single", "multi"):
        raise Error("review_mode must be auto, single or multi.")
    if not isinstance(roles, list) or any(not isinstance(role, str) or role not in ROLES for role in roles):
        raise Error("review_roles must be a list of registered review role IDs.")
    if len(roles) != len(set(roles)):
        raise Error("Duplicate review role selector.")
    if mode == "single" and roles:
        raise Error("Explicit review roles cannot be combined with single mode.")
    if type(max_workers) is not int or not 1 <= max_workers <= len(ROLES):
        raise Error("max_review_workers must be an integer from 1 through 6.")


def make_plan(task, changed, mode="auto", roles=None, max_workers=3):
    roles = [] if roles is None else roles
    validate_options(mode, roles, max_workers)
    paths = [record["path"] for record in changed]
    changed_lines = sum(len(context["changed_lines"]) for context in task["code_context"]
                        if context["status"] != "context")
    components = len({path.split("/")[0] for path in paths if "/" in path})
    signals = {"changed_files": len(paths), "captured_changed_lines": changed_lines,
               "top_level_components": components, "pr_metadata_available": task.get("pull_request") is not None}
    title = (task.get("pull_request") or {}).get("title", "")
    complex_change = len(paths) >= 8 or changed_lines >= 400 or (len(paths) >= 5 and components >= 4)
    reasons = [f"Frozen change signals: {len(paths)} files, {changed_lines} captured changed lines, "
               f"{components} top-level components."]
    selected = []
    if task["knowledge"] and mode != "single" and (roles or mode == "multi" or complex_change):
        selected = list(roles) if roles else ["architecture", "logic"]
        if not roles:
            for role, pattern in (
                ("security", r"auth|permission|credential|secret|token|session|crypto|security"),
                ("reliability", r"client|server|request|resource|lifecycle|retry|error"),
                ("concurrency", r"async|concurr|thread|pool|queue|cache|stream|worker"),
                ("tests", r"test|spec|metric|logging|observab"),
            ):
                if any(re.search(pattern, text, re.IGNORECASE) for text in [*paths, title]):
                    selected.append(role)
        reasons.append("Explicit role selection." if roles else
                       "Explicit multi mode or size threshold; path/title signals select additional lenses.")
    elif not task["knowledge"]:
        reasons.append("No requested semantic rules: static-only preparation does not dispatch host workers.")
    else:
        reasons.append("Single coordinator selected: explicit single mode or below automatic size thresholds.")
    selected = [role for role in ROLES if role in selected]
    identities = [{"knowledge_id": rule["id"], "revision": rule["revision"]} for rule in task["knowledge"]]
    owners = {dimension: next((role for role in selected if dimension in ROLES[role]["dimensions"]),
                              "coordinator") for dimension in DIMENSIONS}
    contract = deepcopy(OUTPUT_CONTRACT)
    contract["repository_assessments"][0]["dimension"] = "only dimensions in this worker's assignment"
    contract["instructions"] = contract["instructions"].replace(
        "First inspect the frozen project context and assess every repository_review dimension.",
        "First inspect the frozen project context and assess only this worker's assigned dimensions.")
    contract["instructions"] += (
        " For a worker packet, assess ONLY assigned repository dimensions and knowledge identities. "
        "Read the same original task.json; do not recapture source or narrow away its context. "
        "Return the original task_id and input_hash, never a worker-specific replacement. "
        "Security is an evidence-checked review dimension, not new policy authority."
    )
    return {
        "version": VERSION, "requested_mode": mode, "planned_mode": "multi" if selected else "single",
        "selected_roles": selected, "max_parallel_workers": max_workers, "signals": signals,
        "dispatch_batches": [selected[index:index + max_workers] for index in range(0, len(selected), max_workers)],
        "reasons": reasons, "dimensions": deepcopy(DIMENSIONS), "dimension_owners": owners,
        "workers": [dict(role_id=role, **deepcopy(ROLES[role]), knowledge=deepcopy(identities))
                    for role in selected],
        "coordinator_knowledge": identities,
        "worker_output_contract": contract,
        "orchestration_contract": {
            "version": VERSION, "actual_mode": "single | multi | serial",
            "workers": [{
                "role_id": "selected role ID", "task_id": "original task_id", "input_hash": "original input_hash",
                "status": "completed | failed | not_run | needs_context", "rationale": "nonempty honest account",
                "response": "seven-key response matching worker_output_contract, or null for failed/not_run",
            }],
            "decisions": [{"action": "accept | merge | reject | conflict",
                           "member_ids": ["task-scoped member IDs"], "rationale": "concrete justification"}],
        },
        "member_id_contract": {
            "coordinator": "TASK_ID/coordinator/ZERO_BASED_FINDING_INDEX",
            "worker": "TASK_ID/worker/ROLE_ID/ZERO_BASED_FINDING_INDEX",
            "static": "TASK_ID/static/FINDING_ID",
        },
        "instructions": (
            "The host coordinator dispatches workers, not this CLI. Pass each worker packet AND its "
            "shared task.json. All roles use identical frozen BASE/HEAD, approved knowledge and context. "
            "Dimensions have exclusive worker/coordinator ownership; approved rules may support several "
            "lenses, so all listed rules are assigned to each worker and still require final coordinator "
            "assessments. Consolidate all dimensions including security in the final response. "
            "Optional response key orchestration follows orchestration_contract; other response keys "
            "remain schema v2. Workers use the seven-key response without recursive orchestration. "
            "Dispatch batches respect max_parallel_workers; the host may use less concurrency. "
            "multi means host-reported subagents, serial means coordinator role passes without independent "
            "subagents, single means no worker passes. Omitted workers remain not_run coverage gaps. "
            "A plan never proves execution. No tool/test execution, provider calls or target edits. "
            "Member IDs are derived from original array positions, before exact deduplication. "
            "Static member IDs use finding_id from the shared task's frozen static_findings array. "
            "Explicit decisions use those IDs; unmentioned findings remain separate. Merge only a "
            "defensible shared root cause and explain why; conflicts remain separate. Rejections retain "
            "raw evidence and cannot suppress static findings. Preserve disagreements and uncertainty."
        ),
        "limitations": [
            "Size and path routing is heuristic, not guaranteed expertise or complete risk coverage.",
            "Captured changed-line counts may undercount omitted or deletion-only source.",
            "Worker execution and model identity are host attestations, not independently verified.",
            "Plans and source content grant no permissions and launch no models or commands.",
        ],
    }


def worker_packets(task):
    return {
        f"worker-{assignment['role_id']}.json": {
            "version": VERSION, "role_id": assignment["role_id"], "task_id": task["task_id"],
            "input_hash": task["input_hash"], "shared_task_path": "task.json",
            "assignment": deepcopy(assignment),
            "output_contract_path": "task.json#/review_plan/worker_output_contract",
        }
        for assignment in task.get("review_plan", {}).get("workers", [])
    }


def member_id(task, source, index):
    return f"{task['task_id']}/{source}/{index}"


def _members(task, findings, source):
    return [dict(finding, member_id=member_id(task, source, index),
                 provenance={"source": source, "role_id": source.split("/")[-1],
                             "task_id": task["task_id"], "input_hash": task["input_hash"]})
            for index, finding in enumerate(findings)]


def consolidate(task, response, coordinator_findings, static_findings, validate_response):
    """Validate host reports using the ordinary evidence validator, then retain all members."""
    plan = task.get("review_plan")
    extension = response.get("orchestration")
    if extension is not None and plan is None:
        raise Error("Orchestration requires a frozen review plan.")
    assignments = {item["role_id"]: item for item in plan["workers"]} if plan else {}
    worker_reports, worker_findings, gaps = {}, [], []
    actual_mode, decisions = "single", []
    if extension is not None:
        _object(extension, ("version", "actual_mode", "workers", "decisions"), "Orchestration")
        if type(extension["version"]) is not int or extension["version"] != VERSION:
            raise Error("Unsupported orchestration version.")
        actual_mode = extension["actual_mode"]
        if not isinstance(actual_mode, str) or actual_mode not in ("single", "multi", "serial"):
            raise Error("Invalid actual review mode.")
        if not isinstance(extension["workers"], list) or not isinstance(extension["decisions"], list):
            raise Error("Workers and decisions must be arrays.")
        if actual_mode != "single" and not assignments:
            raise Error("Worker execution cannot be claimed without selected frozen roles.")
        decisions = extension["decisions"]
        for worker in extension["workers"]:
            _object(worker, ("role_id", "task_id", "input_hash", "status", "rationale", "response"),
                    "Worker report")
            role = worker["role_id"]
            if not isinstance(role, str) or role not in assignments:
                raise Error("Unknown or unselected worker role.")
            if role in worker_reports:
                raise Error("Duplicate worker report; conflicting reports cannot be overwritten.")
            if worker["task_id"] != task["task_id"] or worker["input_hash"] != task["input_hash"]:
                raise Error("Worker report belongs to a stale or different task.")
            status = worker["status"]
            if not isinstance(status, str) or status not in ("completed", "failed", "not_run", "needs_context"):
                raise Error("Invalid worker coverage status.")
            _text(worker["rationale"], "Worker rationale")
            worker_reports[role] = deepcopy(worker)
            if status in ("failed", "not_run"):
                if worker["response"] is not None:
                    raise Error("Failed/not_run workers cannot supply assessments or findings.")
            else:
                if actual_mode == "single":
                    raise Error("Single actual mode cannot claim worker role passes.")
                assignment = assignments[role]
                findings, worker_gaps = validate_response(
                    worker["response"], task, dimensions=assignment["dimensions"],
                    requested_identities={(item["knowledge_id"], item["revision"])
                                          for item in assignment["knowledge"]},
                    allow_orchestration=False)
                worker_findings.extend(_members(task, findings, f"worker/{role}"))
                gaps.extend(f"Worker {role}: {gap}" for gap in worker_gaps)
                if status == "needs_context" and any(
                        assessment["status"] == "checked" for assessment in
                        worker["response"]["repository_assessments"] + worker["response"]["assessments"]):
                    raise Error("A needs_context worker cannot claim checked coverage; use completed with partial gaps.")
        if actual_mode in ("multi", "serial") and not any(
                worker["status"] in ("completed", "needs_context") for worker in worker_reports.values()):
            gaps.append(f"Actual mode {actual_mode} has no completed host role pass.")
    for role in assignments:
        if role not in worker_reports:
            worker_reports[role] = {
                "role_id": role, "task_id": task["task_id"], "input_hash": task["input_hash"],
                "status": "not_run", "rationale": "No host worker report supplied.", "response": None,
            }
        worker = worker_reports[role]
        if worker["status"] != "completed":
            gaps.append(f"Worker {role} {worker['status']}: {worker['rationale']}")
        if actual_mode == "serial":
            gaps.append(f"Worker {role}: serial coordinator fallback; no independent subagent execution attested.")
    if extension is not None and actual_mode in ("multi", "serial"):
        for assessment in response["repository_assessments"]:
            role = plan["dimension_owners"][assessment["dimension"]]
            if role == "coordinator" or assessment["status"] != "checked":
                continue
            worker = worker_reports[role]
            matching = next((item for item in (worker["response"] or {}).get("repository_assessments", [])
                             if item["dimension"] == assessment["dimension"]), None)
            if worker["status"] != "completed" or not matching or matching["status"] != "checked":
                raise Error("Coordinator cannot manufacture checked coverage from an incomplete assigned worker.")
    members = _members(task, coordinator_findings, "coordinator") + worker_findings
    members += [
        dict(finding, member_id=member_id(task, "static", finding["finding_id"]),
             provenance={"source": "static", "role_id": None,
                         "task_id": task["task_id"], "input_hash": task["input_hash"]})
        for finding in static_findings
    ]
    groups = _groups(task, members, decisions)
    rejected = {identity for group in groups if group["action"] == "reject"
                for identity in group["member_ids"]}
    final_assessments = {(assessment["knowledge_id"], assessment["revision"]): assessment
                         for assessment in response["assessments"]}
    for member in worker_findings:
        if member["member_id"] in rejected:
            continue
        assessment = final_assessments.get((member["knowledge_id"], member["revision"]))
        if assessment is None or assessment["status"] != "checked":
            gaps.append(f"Unresolved coordinator/worker rule assessment conflict: {member['member_id']}.")
    if any(group["action"] == "conflict" for group in groups):
        gaps.append("Unresolved consolidation conflict: members remain separate for coordinator review.")
    return {
        "version": VERSION, "planned_mode": plan["planned_mode"] if plan else "single",
        "actual_mode": actual_mode, "execution_evidence": "host_reported_only",
        "workers": [worker_reports[role] for role in assignments],
        "decisions": deepcopy(decisions), "members": members, "groups": groups, "coverage_gaps": gaps,
    }


def _groups(task, members, decisions):
    by_id = {member["member_id"]: member for member in members}
    assigned, groups = set(), []
    for decision in decisions:
        _object(decision, ("action", "member_ids", "rationale"), "Consolidation decision")
        action, ids = decision["action"], decision["member_ids"]
        if not isinstance(action, str) or action not in ("accept", "merge", "reject", "conflict"):
            raise Error("Invalid consolidation action.")
        _text(decision["rationale"], "Consolidation rationale")
        if (not isinstance(ids, list) or not ids or
                any(not isinstance(identity, str) or identity not in by_id for identity in ids)):
            raise Error("Consolidation requires known member IDs from this task.")
        if len(set(ids)) != len(ids) or assigned.intersection(ids):
            raise Error("A finding member cannot be assigned twice.")
        if action in ("accept", "reject") and len(ids) != 1:
            raise Error("Accept/reject decisions require exactly one member.")
        if action in ("merge", "conflict") and len(ids) < 2:
            raise Error("Merge/conflict decisions require at least two members.")
        if action == "reject" and any(by_id[identity]["origin"] == "static" for identity in ids):
            raise Error("Static findings cannot be rejected or suppressed.")
        assigned.update(ids)
        if action == "conflict":
            groups.extend(_group(task, [identity], action, decision["rationale"]) for identity in ids)
        else:
            groups.append(_group(task, ids, action, decision["rationale"]))
    exact = {}
    for member in members:
        if member["member_id"] not in assigned:
            exact.setdefault(member["finding_id"], []).append(member["member_id"])
    for ids in exact.values():
        groups.append(_group(task, ids, "exact_duplicate" if len(ids) > 1 else "accept",
                             "Identical validated finding content." if len(ids) > 1 else
                             "Retained without a host consolidation decision."))
    return sorted(groups, key=lambda group: group["group_id"])


def _group(task, ids, action, rationale):
    ids = sorted(ids)
    return {"group_id": digest({"task_id": task["task_id"], "member_ids": ids, "action": action}),
            "member_ids": ids, "action": action, "rationale": rationale, "display": action != "reject"}
