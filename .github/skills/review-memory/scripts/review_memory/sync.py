"""Resumable collection and a local, non-authoritative learning inbox."""
from __future__ import annotations

from pathlib import Path
import uuid

from . import collect
from .common import Error, digest, load_json, load_yaml, lock, safe_path, utcnow, write_json
from .core import initialize, validate_config
from .propose import load_saved_proposal


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


def _task_reference(root: Path, path: Path, task: dict) -> dict:
    return {
        "task_id": task["task_id"],
        "path": path.relative_to(root).as_posix(),
        "input_hash": task["input_hash"],
        "pr": task["pr"],
        "offline": task["offline"],
    }


def _task_gap(path: Path, reason: str) -> dict:
    return {"kind": "task", "path": path.as_posix(), "reason": reason}


def _proposal_gap(path: Path, reason: str) -> dict:
    return {"kind": "proposal", "path": path.as_posix(), "reason": reason}


def _relevant_task_hint(root: Path, directory: Path, related_task_ids: set[str],
                        repository: str, offline: bool) -> bool:
    try:
        task_path = safe_path(root, directory.relative_to(root) / "task.json")
        if not task_path.exists():
            return True
        task = load_json(task_path)
    except (Error, OSError):
        return True
    if not isinstance(task, dict):
        return True
    task_id = task.get("task_id")
    if isinstance(task_id, str) and task_id in related_task_ids:
        return True
    if task.get("repository") != repository:
        return False
    if type(task.get("offline")) is not bool:
        return True
    return task["offline"] is offline


def _learning_tasks(root: Path, repository: str, offline: bool,
                    state: dict) -> tuple[dict[str, dict], list[dict], set[str], set[str]]:
    tasks: dict[str, dict] = {}
    gaps: list[dict] = []
    seen_paths: set[Path] = set()
    related_task_ids: set[str] = set()
    invalid_task_ids: set[str] = set()
    duplicate_task_ids: set[str] = set()
    tasks_dir = safe_path(root, ".review/local/proposals/tasks")

    def remember(path: Path, task: dict) -> None:
        task_id = task["task_id"]
        existing = tasks.get(task_id)
        if existing is None:
            tasks[task_id] = {"task": task, "path": path, "reference": _task_reference(root, path, task)}
            return
        if existing["path"] == path:
            return
        duplicate_task_ids.add(task_id)
        invalid_task_ids.add(task_id)
        tasks.pop(task_id, None)
        gaps.append(_task_gap(existing["path"].relative_to(root),
                              f"Duplicate learning task ID also exists at {path.relative_to(root).as_posix()}."))
        gaps.append(_task_gap(path.relative_to(root),
                              f"Duplicate learning task ID also exists at {existing['path'].relative_to(root).as_posix()}."))

    for task_id, reference in state["tasks"].items():
        related_task_ids.add(task_id)
        if not isinstance(reference, dict):
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(Path(f".review/local/state/{task_id}.json"), "Invalid sync learning reference."))
            continue
        if not isinstance(reference.get("path"), str) or not reference["path"]:
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(Path(f".review/local/state/{task_id}.json"), "Invalid sync learning task path."))
            continue
        try:
            path = safe_path(root, reference.get("path", ""))
        except Error as exc:
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(Path(f".review/local/state/{task_id}.json"), str(exc)))
            continue
        seen_paths.add(path)
        try:
            task = collect.load_learning_task(root, path, repository=repository)
        except (Error, OSError) as exc:
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(path.relative_to(root), str(exc)))
            continue
        if task["offline"] is not offline:
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(path.relative_to(root), "Sync learning reference points to the wrong offline mode."))
            continue
        if (task["task_id"] != task_id or reference.get("input_hash") != task["input_hash"]
                or reference.get("pr") != task["pr"]):
            invalid_task_ids.add(task_id)
            gaps.append(_task_gap(path.relative_to(root), "Sync learning reference no longer matches its saved task."))
            continue
        remember(path, task)

    if tasks_dir.exists():
        for path in sorted(tasks_dir.glob("*.json")):
            path = safe_path(root, path.relative_to(root))
            if path in seen_paths:
                continue
            try:
                task = collect.load_learning_task(root, path, repository=repository)
            except (Error, OSError) as exc:
                related_task_ids.add(path.stem)
                invalid_task_ids.add(path.stem)
                gaps.append(_task_gap(path.relative_to(root), str(exc)))
                continue
            if task["offline"] is not offline:
                continue
            related_task_ids.add(task["task_id"])
            if task["task_id"] in duplicate_task_ids:
                continue
            remember(path, task)

    return tasks, gaps, related_task_ids, invalid_task_ids


def _learning(root: Path, state: dict, limit: int) -> dict:
    tasks, gaps, related_task_ids, invalid_task_ids = _learning_tasks(
        root, state["repository"], state["offline"], state)
    completed = set()
    groups = {}
    lineage = {}
    proposal_count = 0
    proposals = safe_path(root, ".review/local/proposals")
    for directory in sorted(proposals.iterdir()) if proposals.exists() else []:
        directory = safe_path(root, directory.relative_to(root))
        if directory.name == "tasks" or not directory.is_dir():
            continue
        manifest_path = directory / "proposal.json"
        try:
            manifest = load_json(manifest_path)
        except Error as exc:
            if _relevant_task_hint(root, directory, related_task_ids, state["repository"], state["offline"]):
                gaps.append(_proposal_gap(manifest_path.relative_to(root), str(exc)))
            continue
        if not isinstance(manifest, dict):
            if _relevant_task_hint(root, directory, related_task_ids, state["repository"], state["offline"]):
                gaps.append(_proposal_gap(manifest_path.relative_to(root), "Invalid proposal manifest in learning library."))
            continue
        task_id = manifest.get("task_id")
        if not isinstance(task_id, str):
            if _relevant_task_hint(root, directory, related_task_ids, state["repository"], state["offline"]):
                gaps.append(_proposal_gap(manifest_path.relative_to(root), "Invalid proposal task identity."))
            continue
        if task_id not in related_task_ids and not _relevant_task_hint(
                root, directory, related_task_ids, state["repository"], state["offline"]):
            continue
        task_info = tasks.get(task_id)
        if task_info is None:
            if task_id in invalid_task_ids:
                continue
            gaps.append(_proposal_gap(manifest_path.relative_to(root),
                                      "Saved proposal is bound to an invalid or missing learning task."))
            continue
        try:
            proposal = load_saved_proposal(root, manifest_path, expected_task=task_info["task"])
        except (Error, OSError, UnicodeError) as exc:
            gaps.append(_proposal_gap(manifest_path.relative_to(root), str(exc)))
            continue
        task = proposal["task"]
        for row in proposal["candidates"]:
            knowledge = row["knowledge"]
            candidate_path = row["path"]
            grouping = {key: knowledge.get(key) for key in
                        ("principle", "applicability", "exceptions", "execution", "enforcement", "detector_ref")}
            key = "L-" + collect.digest(grouping)
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
            revisions = lineage.setdefault(knowledge["id"], {})
            revision = revisions.setdefault(knowledge["revision"], {
                "revision": knowledge["revision"], "entry_ids": [], "prs": [],
                "candidate_paths": [], "evidence_ids": [],
            })
            if key not in revision["entry_ids"]:
                revision["entry_ids"].append(key)
            if task["pr"] not in revision["prs"]:
                revision["prs"].append(task["pr"])
            candidate_relative = candidate_path.relative_to(root).as_posix()
            if candidate_relative not in revision["candidate_paths"]:
                revision["candidate_paths"].append(candidate_relative)
            for evidence_id in row["evidence_ids"]:
                if evidence_id not in revision["evidence_ids"]:
                    revision["evidence_ids"].append(evidence_id)
        completed.add(task_id)
        proposal_count += 1
    entries = []
    for item in sorted(groups.values(), key=lambda group: group["id"]):
        entries.append({
            **item,
            "prs": sorted(item["prs"]),
            "proposals": sorted(
                item["proposals"],
                key=lambda proposal: (proposal["knowledge_id"], proposal["revision"], proposal["task_id"], proposal["path"]),
            ),
        })
    lineage_entries = [{
        "knowledge_id": knowledge_id,
        "revisions": sorted(
            (
                {
                    **revision,
                    "entry_ids": sorted(revision["entry_ids"]),
                    "prs": sorted(revision["prs"]),
                    "candidate_paths": sorted(revision["candidate_paths"]),
                    "evidence_ids": sorted(revision["evidence_ids"]),
                    "has_alternatives": len(revision["entry_ids"]) > 1,
                }
                for revision in revisions.values()
            ),
            key=lambda item: item["revision"],
        ),
    } for knowledge_id, revisions in sorted(lineage.items())]
    index_path = safe_path(root, collect.learning_index_relative(state["offline"]))
    write_json(index_path, {
        "schema_version": 1, "repository": state["repository"], "offline": state["offline"],
        "authority": "unapproved", "updated_at": utcnow(), "entries": entries,
        "grouping": "Exact principle and applicability only; semantic merging requires host reasoning.",
        "lineage": lineage_entries,
        "completed_task_ids": sorted(completed),
        "coverage_gaps": gaps,
    })
    pending = [info["reference"] for task_id, info in sorted(
        tasks.items(), key=lambda item: (item[1]["task"]["pr"], item[0]))
               if task_id not in completed]
    learning_prs = {info["task"]["pr"] for info in tasks.values()}
    return {
        "knowledge_path": index_path.relative_to(root).as_posix(),
        "knowledge_count": len(entries), "proposal_count": proposal_count,
        "completed_task_count": len(completed), "pending_task_count": len(pending),
        "pending_tasks": pending[:limit], "pending_tasks_omitted": max(0, len(pending) - limit),
        "learning_pr_count": len(learning_prs),
        "learning_gap_count": len(gaps), "learning_gaps": gaps,
        "learning_complete": not pending and not gaps,
        "approval": "unapproved; human approval remains separate",
    }


def project_status(root: Path, *, offline: bool = False, limit: int = 20) -> dict:
    root = root.resolve()
    if type(limit) is not int or not 1 <= limit <= 100:
        raise Error("limit must be between 1 and 100.")
    config = _configuration(root)
    with lock(root):
        state = _load_state(root, config["repository"], offline)
        learning = _learning(root, state, limit)
        return {"status": "partial" if learning["learning_gap_count"] else "ready",
                "repository": config["repository"], "offline": offline,
                "collection_complete": state["discovery_complete"] and not state["queue"],
                "remaining_pr_count": len(state["queue"]), "collected_pr_count": len(state["seen"]),
                "last_scan_at": state["last_scan_at"], **learning}


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
        if run["learning_gap_count"]:
            run["status"] = "partial"
        write_json(safe_path(root, run_path), run)
        return run
