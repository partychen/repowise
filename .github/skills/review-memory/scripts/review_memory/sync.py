"""Resumable collection and a local, non-authoritative learning inbox."""
from __future__ import annotations

from pathlib import Path
import uuid

from . import collect
from .common import Error, digest, load_json, load_yaml, lock, safe_path, utcnow, write_json
from .core import initialize, validate_config
from .propose import prepare_candidates


def _state_path(root: Path, offline: bool) -> Path:
    return safe_path(root, f".review/local/state/sync{'-fixture' if offline else ''}.json")


def _load_state(root: Path, repository: str, offline: bool) -> dict:
    path = _state_path(root, offline)
    if not path.exists():
        return {"schema_version": 1, "repository": repository, "offline": offline,
                "since": None, "seen": {}, "queue": [], "tasks": {},
                "discovery_complete": False, "last_scan_at": None}
    state = load_json(path)
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or state.get("repository") != repository or state.get("offline") is not offline
            or not isinstance(state.get("seen"), dict) or not isinstance(state.get("tasks"), dict)
            or not isinstance(state.get("queue"), list)
            or type(state.get("discovery_complete")) is not bool
            or "last_scan_at" not in state or "since" not in state):
        raise Error("Invalid project sync state; inspect it rather than resetting progress.")
    numbers = set()
    for item in state["queue"]:
        if not isinstance(item, dict) or not isinstance(item.get("listing_hash"), str):
            raise Error("Invalid sync queue entry.")
        number = collect._number(item.get("pr"), "queued PR")
        if number in numbers:
            raise Error("Duplicate PR in sync queue.")
        numbers.add(number)
    for key, item in state["seen"].items():
        if not key.isdigit() or not isinstance(item, dict) or not isinstance(item.get("listing_hash"), str):
            raise Error("Invalid collected PR state.")
    return state


def _configuration(root: Path) -> dict:
    config = load_yaml(safe_path(root, ".review/config.yaml"))
    validate_config(config)
    return config


def _learning(root: Path, state: dict, limit: int) -> dict:
    registered = {}
    for task_id, reference in state["tasks"].items():
        if not isinstance(reference, dict):
            raise Error("Invalid learning inbox reference.")
        path = safe_path(root, reference.get("path", ""))
        if path.parent != safe_path(root, ".review/local/proposals/tasks"):
            raise Error("Learning inbox task is outside the task directory.")
        task = load_json(path)
        if (not isinstance(task, dict) or task.get("task_id") != task_id
                or task.get("task_type") != "induce" or task.get("repository") != state["repository"]
                or not isinstance(task.get("input"), dict) or not isinstance(task.get("evidence"), list)
                or type(task.get("pr")) is not int or task["pr"] != reference.get("pr")
                or task.get("input_hash") != reference.get("input_hash")
                or digest(task.get("input")) != task.get("input_hash")
                or task.get("evidence") != task["input"].get("evidence")):
            raise Error("Learning inbox task binding changed.")
        registered[task_id] = task
    completed = set()
    groups = {}
    proposal_count = 0
    proposals = safe_path(root, ".review/local/proposals")
    for manifest_path in sorted(proposals.glob("*/proposal.json")):
        manifest_path = safe_path(root, manifest_path.relative_to(root))
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            raise Error("Invalid proposal manifest in learning library.")
        task_id = manifest.get("task_id")
        if task_id not in registered:
            continue
        task = load_json(safe_path(root, manifest_path.parent.relative_to(root) / "task.json"))
        response = load_json(safe_path(root, manifest_path.parent.relative_to(root) / "response.json"))
        if (task != registered[task_id] or not isinstance(response, dict)
                or response.get("task_id") != task_id
                or response.get("input_hash") != task["input_hash"]
                or manifest.get("input_hash") != task["input_hash"]
                or digest(response) != manifest.get("response_hash")
                or digest({"task": task, "response": response}) != manifest_path.parent.name
                or not isinstance(manifest.get("candidates"), list)
                or not isinstance(response.get("candidates"), list)
                or len(manifest["candidates"]) != len(response["candidates"])):
            raise Error("Saved proposal bindings changed; learning completion is not trusted.")
        if manifest["candidates"] != prepare_candidates(root, task, response):
            raise Error("Saved candidate derivation differs from its bound evidence.")
        for row in manifest["candidates"]:
            knowledge = row["knowledge"]
            candidate_path = safe_path(root, manifest_path.parent.relative_to(root)
                                       / f"{knowledge['id']}-r{knowledge['revision']}.yaml")
            if load_yaml(candidate_path) != knowledge:
                raise Error("Saved candidate file differs from its proposal.")
            grouping = {key: knowledge.get(key) for key in
                        ("principle", "applicability", "exceptions", "execution", "enforcement", "detector_ref")}
            key = "L-" + digest(grouping)
            group = groups.setdefault(key, {
                "id": key, "title": knowledge["title"], **grouping,
                "authority": "unapproved", "proposals": [], "prs": [],
            })
            group["proposals"].append({
                "path": str(candidate_path.relative_to(root).as_posix()),
                "manifest": manifest_path.relative_to(root).as_posix(),
                "knowledge_id": knowledge["id"], "revision": knowledge["revision"],
                "evidence_ids": row["evidence_ids"], "task_id": task_id,
            })
            if task["pr"] not in group["prs"]:
                group["prs"].append(task["pr"])
        completed.add(task_id)
        proposal_count += 1
    entries = sorted(groups.values(), key=lambda item: item["id"])
    index_path = safe_path(root, f".review/local/learning/{'fixture-' if state['offline'] else ''}index.json")
    write_json(index_path, {
        "schema_version": 1, "repository": state["repository"], "offline": state["offline"],
        "authority": "unapproved", "updated_at": utcnow(), "entries": entries,
        "grouping": "Exact principle and applicability only; semantic merging requires host reasoning.",
        "completed_task_ids": sorted(completed),
    })
    pending = [{"task_id": task_id, **reference} for task_id, reference in state["tasks"].items()
               if task_id not in completed]
    return {
        "knowledge_path": index_path.relative_to(root).as_posix(),
        "knowledge_count": len(entries), "proposal_count": proposal_count,
        "completed_task_count": len(completed), "pending_task_count": len(pending),
        "pending_tasks": pending[:limit], "pending_tasks_omitted": max(0, len(pending) - limit),
        "learning_complete": not pending, "approval": "unapproved; human approval remains separate",
    }


def project_status(root: Path, *, offline: bool = False, limit: int = 20) -> dict:
    root = root.resolve()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise Error("limit must be between 1 and 100.")
    config = _configuration(root)
    with lock(root):
        state = _load_state(root, config["repository"], offline)
        return {"status": "ready", "repository": config["repository"], "offline": offline,
                "collection_complete": state["discovery_complete"] and not state["queue"],
                "remaining_pr_count": len(state["queue"]), "collected_pr_count": len(state["seen"]),
                "last_scan_at": state["last_scan_at"], **_learning(root, state, limit)}


def sync_project(root: Path, repository: str | None = None, *, max_prs: int = 20,
                 since: str | None = None, refresh: bool = False, fixture: Path | None = None) -> dict:
    root = root.resolve()
    if type(max_prs) is not int or not 1 <= max_prs <= 100:
        raise Error("max_prs must be between 1 and 100.")
    start = collect._date(since, "since").isoformat() if since is not None else None
    if repository is not None:
        repository = collect._repository(repository)
        initialize(root, repository)
    config = _configuration(root)
    repository = config["repository"]
    offline = fixture is not None
    with lock(root):
        state = _load_state(root, repository, offline)
        if state["last_scan_at"] is not None and start is not None and start != state["since"]:
            raise Error("Sync history scope is already bound; use a separate target for a different --since.")
        if state["last_scan_at"] is None and start is not None:
            state["since"] = start
        if refresh and state["queue"]:
            raise Error("Finish the pending sync queue before starting --refresh.")
        store = collect._Store(root, repository)
        run_id = "sync-" + uuid.uuid4().hex
        run_path = f".review/local/runs/{run_id}.json"
        run = {"schema_version": 1, "run_id": run_id, "repository": repository, "offline": offline,
               "started_at": utcnow(), "status": "running", "complete": False,
               "prs": [], "failures": [], "gaps": [], "raw_paths": [], "run_path": run_path}
        write_json(safe_path(root, run_path), run)
        adapter = None
        try:
            adapter = (collect._Fixture(fixture, repository, store) if offline
                       else collect._GitHub(repository, store))
            if not state["queue"]:
                state["discovery_complete"] = False
                write_json(_state_path(root, offline), state)
                listing = (adapter.prs() if offline else adapter.pages(
                    f"repos/{repository}/pulls?state=closed&sort=updated&direction=desc"))
                queue = []
                seen = set()
                for meta in listing:
                    number = collect._number(meta.get("number"), "PR number")
                    if number in seen:
                        raise Error("PR listing changed during pagination; retry discovery.")
                    seen.add(number)
                    if not meta.get("merged_at"):
                        continue
                    collect._validate_metadata(meta, number)
                    if state["since"] and collect._date(meta["merged_at"], "merged_at") < collect._date(state["since"], "since"):
                        continue
                    listing_hash = digest(adapter.items[number] if offline else meta)
                    if refresh or state["seen"].get(str(number), {}).get("listing_hash") != listing_hash:
                        queue.append({"pr": number, "listing_hash": listing_hash})
                state["queue"] = queue
                state["discovery_complete"] = True
                state["last_scan_at"] = utcnow()
                write_json(_state_path(root, offline), state)
        except (Error, OSError) as exc:
            run["failures"].append({"stage": "discovery", "error": str(exc)})
        if not run["failures"]:
            for queued in list(state["queue"][:max_prs]):
                try:
                    result = collect._collect_pr(store, adapter, queued["pr"])
                    state["tasks"][result["task_id"]] = {
                        "path": result["task_path"], "input_hash": result["input_hash"], "pr": result["pr"]}
                    state["seen"][str(queued["pr"])] = {
                        "listing_hash": queued["listing_hash"], "task_id": result["task_id"],
                        "collected_at": utcnow()}
                    state["queue"].remove(queued)
                    run["prs"].append(result["pr"])
                    run["gaps"].extend(result["gaps"])
                    write_json(_state_path(root, offline), state)
                except (Error, OSError) as exc:
                    run["failures"].append({"pr": queued["pr"], "error": str(exc)})
        run["raw_paths"] = list(dict.fromkeys(store.raw))
        run["gaps"] = sorted(set(run["gaps"]))
        run["gaps"].append("Incremental detection uses PR listing metadata; --refresh rechecks unchanged PR feedback.")
        run["finished_at"] = utcnow()
        run["remaining_pr_count"] = len(state["queue"])
        run["collection_complete"] = state["discovery_complete"] and not state["queue"] and not run["failures"]
        run["complete"] = run["collection_complete"]
        run["status"] = "partial" if run["failures"] else ("batch_complete" if state["queue"] else "complete")
        run.update(_learning(root, state, 20))
        write_json(safe_path(root, run_path), run)
        return run
