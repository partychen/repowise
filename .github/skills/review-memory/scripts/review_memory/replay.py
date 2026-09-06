"""Offline, single-arm pipeline replay; this is not an A/B/C/D benchmark.

Dataset v1 contains only ``schema_version``, ``timeline`` (historical/simulated),
``windows`` (train/dev/test, each with inclusive start and exclusive end), and
chronologically ordered ``events``. Review events have id, type="review",
available_at, repository, base, head, trusted_ref, and optional group="D".
Feedback events have only id, type="feedback", available_at, and review_id;
their contents are never ingested. All revisions are full immutable commit SHAs.

Labels v1 are a separate JSON document with schema_version, replay_id,
adjudicator={identity, kind:"human", independent:true}, and cases. Each case has
case_id, reviewed_at, rationale, normal_control, issues=[{id}], and judgments.
Judgments have finding_id, disposition, rationale, optional issue_id, and optional
true_positive. Only an explicit true_positive=true on a valid judgment with an
issue_id counts as a TP. Identities and independence are human attestations,
not cryptographically verified identities.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from pathlib import Path

from .common import (
    Error, digest, git, load_json, resolve_commit, safe_path, timestamp,
    utcnow, write_json,
)


DATASET_SCHEMA = 1
LABEL_SCHEMA = 1
SPLITS = ("train", "dev", "test")
DISPOSITIONS = {
    "valid", "false_positive", "waived", "out_of_scope", "obsolete",
    "deferred", "duplicate", "unjudged",
}
LIMITATIONS = [
    "Reduced single-arm D pipeline pilot; no A/B/C/D benefit measurement.",
    "Train/dev/test are declared chronological partitions, not model training.",
    "Feedback metadata is counted but feedback is not ingested or harvested.",
    "Git commit times and declared availability are not proof of historical availability.",
    "Contemporary signatures do not prove policy availability at a historical review time.",
    "Signed approval requested_at and source available_at cannot be backdated by replay.",
    "Pinned signatures establish approved content provenance, not a full online reconstruction.",
    "Human adjudicator identity and independence are attestations, not verified identities.",
    "Model contamination, missing historical observations, and unobserved costs are unknown.",
]
RECONSTRUCTION_GAPS = [
    "Historical policy availability has no independently verified reconstruction provenance.",
    "Contemporary approvals are eligible only at or after their signed availability; "
    "use current-time simulated events to exercise newly approved policies.",
]


def _object(value, required, optional=(), label="object"):
    if not isinstance(value, dict):
        raise Error(f"{label} must be an object.")
    missing, extra = set(required) - value.keys(), value.keys() - set(required) - set(optional)
    if missing or extra:
        raise Error(f"{label} has missing {sorted(missing)} or unsupported {sorted(extra)} fields.")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise Error(f"{label} must be nonempty text.")


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise Error(f"{label} must be a safe identifier of 1-100 characters.")


def _array(value, label):
    if not isinstance(value, list):
        raise Error(f"{label} must be a list.")


def _read(path):
    try:
        if path.stat().st_size > 2_000_000:
            raise Error("Replay JSON exceeds the 2 MB limit.")
    except OSError as exc:
        raise Error(f"Cannot read replay JSON: {path}") from exc
    return load_json(path)


def _dataset(data):
    _object(data, {"schema_version", "timeline", "windows", "events"}, label="dataset")
    if type(data["schema_version"]) is not int or data["schema_version"] != DATASET_SCHEMA:
        raise Error("Unsupported replay dataset schema.")
    if not isinstance(data["timeline"], str) or data["timeline"] not in {"historical", "simulated"}:
        raise Error("timeline must be historical or simulated.")
    _object(data["windows"], SPLITS, label="windows")
    windows, summary = {}, {}
    previous_end = None
    for name in SPLITS:
        window = data["windows"][name]
        _object(window, {"start", "end"}, label=f"windows.{name}")
        start, end = timestamp(window["start"]), timestamp(window["end"])
        if end <= start or (previous_end is not None and start < previous_end):
            raise Error("Train/dev/test windows must be ordered, nonoverlapping, and nonempty.")
        windows[name] = (start, end)
        summary[name] = {
            **window, "event_count": 0, "review_count": 0, "feedback_count": 0,
            "first_event_at": None, "last_event_at": None,
        }
        previous_end = end
    _array(data["events"], "events")
    if not data["events"] or len(data["events"]) > 1000:
        raise Error("Dataset must contain 1-1000 events.")
    ids, reviews, last_at, assigned = set(), {}, None, []
    for event in data["events"]:
        if not isinstance(event, dict):
            raise Error("Event must be an object.")
        if event.get("type") == "review":
            _object(event, {"id", "type", "available_at", "repository", "base", "head", "trusted_ref"},
                    {"group"}, "review event")
            if event.get("group", "D") != "D":
                raise Error("Only group D is supported; this is not an A/B/C/D comparison.")
            repository = event["repository"]
            if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise Error("repository must be owner/name.")
            for field in ("base", "head", "trusted_ref"):
                if not isinstance(event[field], str) or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", event[field]):
                    raise Error(f"{field} must be a full lowercase commit SHA.")
        elif event.get("type") == "feedback":
            _object(event, {"id", "type", "available_at", "review_id"}, label="feedback event")
            _identifier(event["review_id"], "review_id")
        else:
            raise Error("Only review or payload-free feedback events are supported.")
        _identifier(event["id"], "event.id")
        if event["id"] in ids:
            raise Error("Event ids must be unique.")
        ids.add(event["id"])
        at = timestamp(event["available_at"])
        if last_at is not None and at < last_at:
            raise Error("Events must be in chronological availability order.")
        last_at = at
        split = next((name for name, (start, end) in windows.items() if start <= at < end), None)
        if split is None:
            raise Error("Every event must be inside exactly one declared window.")
        if event["type"] == "feedback":
            if event["review_id"] not in reviews or at <= reviews[event["review_id"]]:
                raise Error("Feedback must be strictly later than its preceding review.")
        else:
            reviews[event["id"]] = at
        counts = summary[split]
        counts["event_count"] += 1
        counts[event["type"] + "_count"] += 1
        counts["first_event_at"] = counts["first_event_at"] or event["available_at"]
        counts["last_event_at"] = event["available_at"]
        assigned.append((event, split))
    if not reviews:
        raise Error("Dataset requires at least one review event.")
    return assigned, summary


def _commit_times(root, event):
    times = {}
    for field in ("base", "head", "trusted_ref"):
        sha = event[field]
        if resolve_commit(root, sha) != sha:
            raise Error(f"{field} does not identify a commit directly.")
        committed_at = git(root, "show", "-s", "--format=%cI", sha)
        if timestamp(committed_at) > timestamp(event["available_at"]):
            raise Error(f"{field} commit time is after review availability; future context is forbidden.")
        times[field] = committed_at
    return times


def prepare_replay(root: Path, dataset_path: Path) -> dict:
    """Prepare real production-engine tasks without touching production run state."""
    from .review import prepare_review

    root = Path(root).resolve()
    dataset = _read(Path(dataset_path))
    assigned, windows = _dataset(dataset)
    # Validate every revision before creating any tasks, including later windows.
    times = {event["id"]: _commit_times(root, event)
             for event, _ in assigned if event["type"] == "review"}
    replay_id = "replay-" + uuid.uuid4().hex
    directory = safe_path(root, f".review/local/evaluation/{replay_id}")
    directory.mkdir(parents=True, exist_ok=False)
    runs = []
    try:
        for event, split in assigned:
            if event["type"] != "review":
                continue
            prepared = prepare_review(
                root, event["repository"], event["base"], event["head"], event["trusted_ref"],
                max_files=100, max_bytes=500000, at=event["available_at"],
                runs_root=directory / "runs",
            )
            run_id = prepared["run_id"]
            _identifier(run_id, "run_id")
            run_directory = safe_path(directory, f"runs/{run_id}")
            task = _read(safe_path(run_directory, "task.json"))
            request = _read(safe_path(run_directory, "request.json"))
            runs.append({
                "case_id": event["id"], "split": split, "group": "D",
                "available_at": event["available_at"], "repository": event["repository"],
                "base": event["base"], "head": event["head"], "trusted_ref": event["trusted_ref"],
                "commit_times": times[event["id"]], "run_id": run_id,
                "task_hash": digest(task), "request_hash": digest(request),
                "requires_response": prepared["requires_response"],
            })
        manifest = {
            "schema_version": 1, "replay_id": replay_id, "created_at": utcnow(),
            "status": "prepared", "mode": "single_arm_pipeline_pilot",
            "timeline": dataset["timeline"], "dataset": dataset, "dataset_hash": digest(dataset),
            "reconstruction_status": "simulation_only" if dataset["timeline"] == "simulated" else "unverified",
            "reconstruction_gaps": RECONSTRUCTION_GAPS,
            "windows": windows, "runs": runs, "run_list_hash": digest(runs),
            "limitations": LIMITATIONS,
        }
        manifest["manifest_hash"] = digest(manifest)
        write_json(safe_path(directory, "manifest.json"), manifest)
    except (Error, OSError, UnicodeError) as exc:
        write_json(safe_path(directory, "failed.json"), {
            "status": "failed", "replay_id": replay_id, "reason": str(exc),
            "notice": "Partial tasks are not a prepared or scoreable replay.",
        })
        raise
    return {
        "replay_id": replay_id, "mode": manifest["mode"], "timeline": dataset["timeline"],
        "manifest_path": str(directory / "manifest.json"), "runs_root": str(directory / "runs"),
        "dataset_hash": manifest["dataset_hash"], "run_list_hash": manifest["run_list_hash"],
        "reconstruction_status": manifest["reconstruction_status"],
        "reconstruction_gaps": manifest["reconstruction_gaps"],
        "windows": windows, "runs": runs, "limitations": LIMITATIONS,
    }


def _labels(data, replay_id, case_ids):
    _object(data, {"schema_version", "replay_id", "adjudicator", "cases"}, label="labels")
    if type(data["schema_version"]) is not int or data["schema_version"] != LABEL_SCHEMA:
        raise Error("Unsupported adjudication label schema.")
    if data["replay_id"] != replay_id:
        raise Error("Labels must name this frozen replay.")
    reviewer = data["adjudicator"]
    _object(reviewer, {"identity", "kind", "independent"}, label="adjudicator")
    _text(reviewer["identity"], "adjudicator.identity")
    if reviewer["kind"] != "human" or reviewer["independent"] is not True:
        raise Error("Independent human adjudication is required; model self-scores are not labels.")
    _array(data["cases"], "label cases")
    labels = {}
    for case in data["cases"]:
        _object(case, {"case_id", "reviewed_at", "rationale", "normal_control", "issues", "judgments"},
                label="case adjudication")
        _identifier(case["case_id"], "case_id")
        if case["case_id"] not in case_ids or case["case_id"] in labels:
            raise Error("Label case must uniquely name a frozen replay case.")
        timestamp(case["reviewed_at"])
        _text(case["rationale"], "case rationale")
        if type(case["normal_control"]) is not bool:
            raise Error("normal_control must be boolean.")
        _array(case["issues"], "issues")
        issue_ids = set()
        for issue in case["issues"]:
            _object(issue, {"id"}, label="problem instance")
            _identifier(issue["id"], "issue.id")
            if issue["id"] in issue_ids:
                raise Error("Problem-instance issue ids must be unique within a case.")
            issue_ids.add(issue["id"])
        if case["normal_control"] and issue_ids:
            raise Error("Normal controls cannot contain known problem instances.")
        _array(case["judgments"], "judgments")
        findings = set()
        for judgment in case["judgments"]:
            _object(judgment, {"finding_id", "disposition", "rationale"},
                    {"issue_id", "true_positive"}, "finding judgment")
            _identifier(judgment["finding_id"], "finding_id")
            if judgment["finding_id"] in findings:
                raise Error("Each finding may have only one adjudication mapping.")
            findings.add(judgment["finding_id"])
            if not isinstance(judgment["disposition"], str) or judgment["disposition"] not in DISPOSITIONS:
                raise Error("Unsupported finding disposition.")
            _text(judgment["rationale"], "judgment rationale")
            if "true_positive" in judgment and type(judgment["true_positive"]) is not bool:
                raise Error("true_positive must be an explicit boolean.")
            if "issue_id" in judgment:
                _identifier(judgment["issue_id"], "issue_id")
                if judgment["issue_id"] not in issue_ids:
                    raise Error("Matched issue_id must identify a labeled problem instance.")
            if judgment.get("true_positive") is True:
                if judgment["disposition"] != "valid" or "issue_id" not in judgment:
                    raise Error("A TP requires disposition valid and a labeled issue_id.")
        labels[case["case_id"]] = case
    return labels


def _case_metrics(case_id, report, label):
    findings = report.get("findings")
    _array(findings, "report findings")
    finding_ids = set()
    for finding in findings:
        if not isinstance(finding, dict):
            raise Error("Report findings must be objects.")
        finding_id = finding.get("finding_id")
        _identifier(finding_id, "report finding id")
        if finding_id in finding_ids:
            raise Error("Report finding ids must be unique.")
        finding_ids.add(finding_id)
    judgments = {j["finding_id"]: j for j in label["judgments"]} if label else {}
    if set(judgments) - finding_ids:
        raise Error("Adjudication references a finding absent from the finished report.")
    counts, matched, unjudged, duplicate_matches = Counter(), set(), 0, 0
    for finding_id in sorted(finding_ids):
        judgment = judgments.get(finding_id)
        disposition = judgment["disposition"] if judgment else "unjudged"
        counts[disposition] += 1
        if disposition == "valid" and judgment.get("true_positive") is True:
            if judgment["issue_id"] in matched:
                duplicate_matches += 1
            matched.add(judgment["issue_id"])
        elif disposition in {"unjudged", "valid"}:
            unjudged += 1
    return {
        "case_id": case_id, "labeled": label is not None,
        "finding_count": len(findings), "true_positives": len(matched),
        "false_positives": counts["false_positive"], "unjudged": unjudged,
        "dispositions": dict(counts), "duplicate_tp_matches_excluded": duplicate_matches,
        "problem_instances": len(label["issues"]) if label else None,
        "matched_problem_instances": len(matched), "normal_control": label["normal_control"] if label else None,
        "observed_cost": None, "cost_status": "unknown",
        "review_status": report["status"], "coverage_gaps": report.get("coverage_gaps", []),
    }


def _aggregate(cases):
    tp = sum(c["true_positives"] for c in cases)
    fp = sum(c["false_positives"] for c in cases)
    known_issues = sum(c["problem_instances"] or 0 for c in cases)
    matched = sum(c["matched_problem_instances"] for c in cases)
    controls = [c for c in cases if c["normal_control"] is True]
    unknown_controls = sum(c["unjudged"] > 0 for c in controls)
    inappropriate = sum(c["false_positives"] > 0 for c in controls)
    dispositions = Counter()
    for case in cases:
        dispositions.update(case["dispositions"])
    return {
        "case_count": len(cases), "labeled_case_count": sum(c["labeled"] for c in cases),
        "unlabeled_case_count": sum(not c["labeled"] for c in cases),
        "finding_count": sum(c["finding_count"] for c in cases),
        "true_positives": tp, "false_positives": fp,
        "unjudged": sum(c["unjudged"] for c in cases),
        "dispositions": dict(dispositions),
        "precision": tp / (tp + fp) if tp + fp else None,
        "precision_denominator": tp + fp,
        "problem_instances": known_issues, "matched_problem_instances": matched,
        "recall": matched / known_issues if known_issues else None,
        "recall_denominator": known_issues,
        "duplicate_tp_matches_excluded": sum(c["duplicate_tp_matches_excluded"] for c in cases),
        "normal_control_cases": len(controls),
        "normal_control_cases_with_alerts": sum(c["finding_count"] > 0 for c in controls),
        "normal_control_inappropriate_alert_cases": inappropriate,
        "normal_control_unknown_cases": unknown_controls,
        "normal_control_inappropriate_alert_rate": (
            inappropriate / len(controls) if controls and not unknown_controls else None
        ),
        "observed_cost": None, "unknown_cost_cases": len(cases),
        "coverage_incomplete_cases": sum(c["review_status"] != "complete" for c in cases),
    }


def score_replay(root: Path, replay_id: str, labels_path: Path) -> dict:
    """Score only completed frozen tasks, using separate independent human labels."""
    _identifier(replay_id, "replay_id")
    root = Path(root).resolve()
    directory = safe_path(root, f".review/local/evaluation/{replay_id}")
    manifest = _read(safe_path(directory, "manifest.json"))
    if not isinstance(manifest, dict):
        raise Error("Replay manifest must be an object.")
    stored_hash = manifest.get("manifest_hash")
    if stored_hash != digest({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        raise Error("Frozen replay manifest changed.")
    if manifest.get("schema_version") != 1 or manifest.get("replay_id") != replay_id or manifest.get("status") != "prepared":
        raise Error("Replay manifest is not a prepared v1 replay.")
    if digest(manifest["dataset"]) != manifest["dataset_hash"] or digest(manifest["runs"]) != manifest["run_list_hash"]:
        raise Error("Frozen dataset or run linkage changed.")
    _dataset(manifest["dataset"])
    reports, report_hashes, tasks = {}, {}, {}
    for run in manifest["runs"]:
        _identifier(run["run_id"], "run_id")
        run_directory = safe_path(directory, "runs/" + run["run_id"])
        task = _read(safe_path(run_directory, "task.json"))
        request = _read(safe_path(run_directory, "request.json"))
        if digest(task) != run["task_hash"] or digest(request) != run["request_hash"]:
            raise Error("Frozen production task or request changed.")
        completion_path = safe_path(run_directory, "completion.json")
        if not completion_path.is_file():
            raise Error(f"Review {run['case_id']} is unfinished: completion.json is missing.")
        completion = _read(completion_path)
        report = _read(safe_path(run_directory, "report.json"))
        if not isinstance(completion, dict) or completion.get("report") != report:
            raise Error("Finished report does not match its completion record.")
        if completion.get("completion_hash") != digest(
                {k: v for k, v in completion.items() if k != "completion_hash"}):
            raise Error("Stored completion hash does not match the finished record.")
        if run["requires_response"]:
            response = _read(safe_path(run_directory, "response.json"))
            if completion.get("response_hash") != digest(response):
                raise Error("Stored host response does not match the completion record.")
        elif completion.get("response_hash") is not None:
            raise Error("Static-only completion must not claim a host response.")
        if not isinstance(report, dict) or report.get("awaiting_response") is not False:
            raise Error("Cannot score an unfinished review report.")
        if report.get("status") not in {"complete", "incomplete"}:
            raise Error("Unknown review report status.")
        for field in ("run_id", "task_id", "input_hash", "repository", "base_sha", "head_sha",
                      "trusted_sha", "approved_snapshot_hash", "runtime"):
            if field not in report or report[field] != task.get(field):
                raise Error(f"Finished report has mismatched {field} linkage.")
        reports[run["case_id"]] = report
        report_hashes[run["case_id"]] = digest(report)
        tasks[run["case_id"]] = task
    data = _read(Path(labels_path))
    labels = _labels(data, replay_id, set(reports))
    cases = []
    for run in manifest["runs"]:
        case_id, label = run["case_id"], labels.get(run["case_id"])
        if label and timestamp(label["reviewed_at"]) < timestamp(tasks[case_id]["created_at"]):
            raise Error("Human adjudication cannot precede production task creation.")
        metrics = _case_metrics(case_id, reports[case_id], label)
        cases.append(dict(metrics, split=run["split"], run_id=run["run_id"]))
    result = {
        "schema_version": 1, "replay_id": replay_id, "scored_at": utcnow(),
        "mode": "single_arm_pipeline_pilot", "timeline": manifest["timeline"],
        "reconstruction_status": manifest["reconstruction_status"],
        "reconstruction_gaps": manifest["reconstruction_gaps"],
        "manifest_hash": stored_hash, "dataset_hash": manifest["dataset_hash"],
        "run_list_hash": manifest["run_list_hash"], "report_hashes": report_hashes,
        "labels_hash": digest(data), "adjudicator": data["adjudicator"],
        "windows": manifest["windows"], "cases": cases,
        "by_split": {name: _aggregate([c for c in cases if c["split"] == name]) for name in SPLITS},
        "metric_scope": (
            "Precision is confirmed TP/(TP+FP), excluding duplicate TP matches and unresolved/nonbinary "
            "dispositions. Recall uses distinct labeled problem instances per case, not rule agreement. "
            "Partial human labels and bounded review coverage are not whole-dataset correctness."
        ),
        "limitations": LIMITATIONS,
    }
    # Preserve each adjudication revision rather than silently replacing a previous score.
    score_id = digest({"labels": data, "reports": report_hashes, "manifest": stored_hash})
    score_path = safe_path(directory, f"scores/{score_id}.json")
    if score_path.exists():
        previous = _read(score_path)
        if previous.get("score_hash") != digest({k: v for k, v in previous.items() if k != "score_hash"}):
            raise Error("Stored score was modified.")
        if digest(_read(safe_path(directory, f"adjudications/{score_id}.json"))) != digest(data):
            raise Error("Stored independent adjudication was modified.")
        return previous
    result["score_path"] = str(score_path)
    result["adjudication_path"] = str(safe_path(directory, f"adjudications/{score_id}.json"))
    result["score_hash"] = digest(result)
    write_json(safe_path(directory, f"adjudications/{score_id}.json"), data)
    write_json(score_path, result)
    return result
