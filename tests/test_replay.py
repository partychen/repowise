"""Offline replay fixtures live only in disposable project-local directories."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "review-memory" / "scripts"))

from review_memory import core, replay, review
from review_memory.common import Error, digest, load_json, runtime_info, utcnow, write_json, write_yaml


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".test-replay-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup)
        self.command("init", "--quiet")
        self.command("config", "user.email", "replay@example.invalid")
        self.command("config", "user.name", "Replay fixture")
        self.command("config", "commit.gpgsign", "false")
        self.command("config", "core.autocrlf", "false")
        self.command("config", "core.hooksPath", str(self.root / "no-hooks"))
        core.initialize(self.root, "owner/repo")
        (self.root / "source.py").write_bytes(b"before = 1\n")
        self.commit()
        self.base = self.command("rev-parse", "HEAD")
        (self.root / "source.py").write_bytes(b"after = 2\n")
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        self.dataset = {
            "schema_version": 1, "timeline": "simulated",
            "windows": {
                "train": {"start": "2026-01-01T00:00:00Z", "end": "2026-02-01T00:00:00Z"},
                "dev": {"start": "2026-02-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"},
                "test": {"start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
            },
            "events": [self.event("case-1", "2026-03-01T00:00:00Z")],
        }
        self.dataset_path = self.root / "dataset.json"
        self.labels_path = self.root / "independent-labels.json"

    def cleanup(self):
        def writable(function, path, error):
            os.chmod(path, 0o700)
            for attempt in range(20):
                try:
                    function(path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        shutil.rmtree(self.root, onerror=writable)

    def command(self, *args):
        env = dict(os.environ, GIT_AUTHOR_DATE="2025-01-01T00:00:00Z",
                   GIT_COMMITTER_DATE="2025-01-01T00:00:00Z")
        result = subprocess.run(["git", "-C", str(self.root), "--no-pager", *args],
                                env=env, capture_output=True, check=True)
        return result.stdout.decode("utf-8").strip()

    def commit(self):
        self.command("add", "--all")
        self.command("commit", "--quiet", "-m", "Offline replay fixture")

    def event(self, case_id, at):
        return {"id": case_id, "type": "review", "available_at": at, "repository": "owner/repo",
                "base": self.base, "head": self.head, "trusted_ref": self.base, "group": "D"}

    def prepare(self):
        write_json(self.dataset_path, self.dataset)
        return replay.prepare_replay(self.root, self.dataset_path)

    def run_directory(self, prepared, index=0):
        return Path(prepared["runs_root"]) / prepared["runs"][index]["run_id"]

    def labels(self, prepared, judgments=None, issues=None, normal=False):
        return {
            "schema_version": 1, "replay_id": prepared["replay_id"],
            "adjudicator": {"identity": "independent-reviewer", "kind": "human", "independent": True},
            "cases": [{"case_id": "case-1", "reviewed_at": utcnow(),
                       "rationale": "Human reviewed the frozen report and code.",
                       "normal_control": normal, "issues": issues or [], "judgments": judgments or []}],
        }

    def score(self, prepared, labels):
        write_json(self.labels_path, labels)
        return replay.score_replay(self.root, prepared["replay_id"], self.labels_path)

    def semantic_snapshot(self):
        rules = []
        for name in ("K-one", "K-two", "K-three"):
            rules.append({
                "id": name, "revision": 1, "execution": "llm", "principle": "Inspect changed assignment.",
                "applicability": {"paths": ["*.py"], "exclusions": [], "context": ""},
            })
        return {
            "hash": "fixture-snapshot", "trusted_sha": self.base, "knowledge": rules,
            "detectors": [], "manifest": {"coverage_gaps": []},
            "config": {"repository": "owner/repo", "max_rules_per_run": 50,
                       "max_findings_per_pr": 10, "model_provider": "fixture"},
        }

    def finish_semantic(self, prepared):
        directory = self.run_directory(prepared)
        task = load_json(directory / "task.json")
        response = {
            "schema_version": 1, "task_id": task["task_id"], "input_hash": task["input_hash"],
            "model": {"provider": "fixture", "model": "offline-fixture", "prompt_version": "v1"},
            "assessments": [
                {"knowledge_id": rule["id"], "revision": 1, "status": "checked",
                 "rationale": "Fixture assessment, not model benchmark evidence."}
                for rule in task["knowledge"]
            ],
            "findings": [
                {
                    "knowledge_id": rule["id"], "revision": 1,
                    "evidence": {"path": "source.py", "line_start": 1, "line_end": 1, "text": "after = 2"},
                    "applicability_rationale": "Changed assignment is within fixture scope.",
                    "counterexample_checks": ["Compared prior assignment."],
                    "impact": "Fixture issue", "triggering_conditions": "Fixture condition",
                    "suggestion": "Inspect the assignment.", "uncertainty": "Offline fixture only.",
                    "verification": "inspected",
                } for rule in task["knowledge"]
            ],
        }
        response_path = self.root / "response.json"
        write_json(response_path, response)
        return review.finalize_review(self.root, task["run_id"], response_path,
                                      runs_root=Path(prepared["runs_root"]))

    def test_real_engine_isolated_pinned_and_window_counts(self):
        self.dataset["events"] = [
            self.event("train-case", "2026-01-31T23:59:59Z"),
            self.event("dev-case", "2026-02-01T00:00:00Z"),
            self.event("case-1", "2026-03-01T00:00:00Z"),
            {"id": "feedback-1", "type": "feedback", "available_at": "2026-03-02T00:00:00Z",
             "review_id": "case-1"},
        ]
        sentinel = self.root / ".review" / "local" / "runs" / "sentinel"
        sentinel.write_text("production-state", encoding="utf-8")
        before_head = self.command("rev-parse", "HEAD")
        self.labels_path.write_text("NOT JSON; must never be read while preparing", encoding="utf-8")
        (self.root / "source.py").write_text("dirty = 999\n", encoding="utf-8")
        prepared = self.prepare()
        self.assertEqual("single_arm_pipeline_pilot", prepared["mode"])
        self.assertEqual("simulation_only", prepared["reconstruction_status"])
        self.assertTrue(prepared["reconstruction_gaps"])
        self.assertEqual(2, prepared["windows"]["test"]["event_count"])
        self.assertEqual(1, prepared["windows"]["test"]["review_count"])
        self.assertEqual(1, prepared["windows"]["test"]["feedback_count"])
        self.assertEqual(1, prepared["windows"]["train"]["event_count"])
        self.assertEqual(1, prepared["windows"]["dev"]["event_count"])
        self.assertEqual(before_head, self.command("rev-parse", "HEAD"))
        self.assertEqual([sentinel], list(sentinel.parent.iterdir()))
        task = load_json(self.run_directory(prepared, 2) / "task.json")
        self.assertEqual("2026-03-01T00:00:00Z", task["effective_at"])
        self.assertEqual("after = 2\n", task["code_context"][0]["after"])
        self.assertNotIn("feedback-1", json.dumps(task))
        self.assertEqual("production-state", sentinel.read_text(encoding="utf-8"))
        self.assertEqual(digest(self.dataset), prepared["dataset_hash"])

    def test_manifest_rejects_answers_future_feedback_and_unpinned_refs(self):
        variants = []
        answers = copy.deepcopy(self.dataset)
        answers["answer_key"] = "forbidden"
        variants.append(answers)
        answers = copy.deepcopy(self.dataset)
        answers["events"][0]["labels_path"] = "secret.json"
        variants.append(answers)
        future = copy.deepcopy(self.dataset)
        future["events"].insert(0, {"id": "feedback", "type": "feedback", "review_id": "case-1",
                                    "available_at": "2026-03-01T00:00:00Z"})
        variants.append(future)
        unordered = copy.deepcopy(self.dataset)
        unordered["events"].append(self.event("earlier", "2026-02-01T00:00:00Z"))
        variants.append(unordered)
        unpinned = copy.deepcopy(self.dataset)
        unpinned["events"][0]["trusted_ref"] = "HEAD"
        variants.append(unpinned)
        comparison = copy.deepcopy(self.dataset)
        comparison["events"][0]["group"] = "A"
        variants.append(comparison)
        overlap = copy.deepcopy(self.dataset)
        overlap["windows"]["dev"]["start"] = "2026-01-31T00:00:00Z"
        variants.append(overlap)
        content = copy.deepcopy(self.dataset)
        content["events"].append({"id": "feedback", "type": "feedback", "review_id": "case-1",
                                  "available_at": "2026-03-02T00:00:00Z", "content": "future hint"})
        variants.append(content)
        for dataset in variants:
            with self.subTest(dataset=dataset), self.assertRaises(Error):
                replay._dataset(dataset)
        self.assertEqual([], list((self.root / ".review" / "local" / "evaluation").iterdir()))

    def test_future_commit_timestamp_rejected(self):
        with patch("review_memory.replay.git", return_value="2026-03-02T00:00:00Z"):
            with self.assertRaisesRegex(Error, "future context"):
                self.prepare()

    def test_future_source_and_approval_excluded_by_production_snapshot(self):
        rule = self.semantic_snapshot()["knowledge"][0]
        rule.update(schema_version=1, maturity="approved", title="Future rule",
                    effective_from="2025-01-01T00:00:00Z",
                    sources=[{"kind": "history", "reference": "fixture", "version": "1",
                              "available_at": "2026-03-02T00:00:00Z"}])
        payload = {"kind": "knowledge", "repository": "owner/repo", "runtime": runtime_info(),
                   "requested_at": "2025-01-01T00:00:00Z", "content": rule}
        record = {"payload": payload, "recorded_at": "2025-01-01T00:00:00Z", "signature": "test-only"}
        write_yaml(self.root / ".review" / "knowledge" / "K-one-r1.yaml", rule)
        write_json(self.root / ".review" / "approvals" / "future.json", record)
        self.commit()
        policy_sha = self.command("rev-parse", "HEAD")
        (self.root / "source.py").write_bytes(b"after = 3\n")
        self.commit()
        self.dataset["events"][0].update(
            trusted_ref=policy_sha, base=policy_sha, head=self.command("rev-parse", "HEAD"))
        # Only signature verification is stubbed; the production temporal filter is exercised.
        with patch("review_memory.core.verify_signature"):
            prepared = self.prepare()
        task = load_json(self.run_directory(prepared) / "task.json")
        self.assertEqual([], task["knowledge"])
        self.assertFalse(prepared["runs"][0]["requires_response"])
        self.assertNotIn("Future rule", json.dumps(task))
        rule["sources"][0]["available_at"] = "2025-01-01T00:00:00Z"
        record["recorded_at"] = "2026-03-02T00:00:00Z"
        write_yaml(self.root / ".review" / "knowledge" / "K-one-r1.yaml", rule)
        write_json(self.root / ".review" / "approvals" / "future.json", record)
        self.commit()
        policy_sha = self.command("rev-parse", "HEAD")
        (self.root / "source.py").write_bytes(b"after = 4\n")
        self.commit()
        self.dataset["events"][0].update(
            trusted_ref=policy_sha, base=policy_sha, head=self.command("rev-parse", "HEAD"))
        with patch("review_memory.core.verify_signature"):
            second = self.prepare()
        second_task = load_json(self.run_directory(second) / "task.json")
        self.assertEqual([], second_task["knowledge"])

    def test_empty_reports_unknown_precision_and_cost(self):
        prepared = self.prepare()
        labels = self.labels(prepared, normal=True)
        score = self.score(prepared, labels)
        metrics = score["by_split"]["test"]
        self.assertEqual("simulation_only", score["reconstruction_status"])
        self.assertTrue(score["reconstruction_gaps"])
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])
        self.assertIsNone(metrics["observed_cost"])
        self.assertEqual(1, metrics["unknown_cost_cases"])
        self.assertEqual(0, metrics["normal_control_inappropriate_alert_rate"])
        self.assertEqual(score, self.score(prepared, labels))
        self.assertTrue(Path(score["score_path"]).exists())

    def test_finished_findings_explicit_tp_unique_issue_recall(self):
        with patch("review_memory.core.load_git_snapshot", return_value=self.semantic_snapshot()):
            prepared = self.prepare()
        report = self.finish_semantic(prepared)
        ids = [f["finding_id"] for f in report["findings"]]
        judgments = [
            {"finding_id": ids[0], "disposition": "valid", "true_positive": True,
             "issue_id": "problem-1", "rationale": "Confirmed real problem."},
            {"finding_id": ids[1], "disposition": "valid", "true_positive": True,
             "issue_id": "problem-1", "rationale": "Same problem, different rule."},
            {"finding_id": ids[2], "disposition": "false_positive", "rationale": "Not a real problem."},
        ]
        labels = self.labels(prepared, judgments, [{"id": "problem-1"}, {"id": "problem-2"}])
        score = self.score(prepared, labels)
        metrics = score["by_split"]["test"]
        self.assertEqual(1, metrics["true_positives"])
        self.assertEqual(1, metrics["false_positives"])
        self.assertEqual(1, metrics["duplicate_tp_matches_excluded"])
        self.assertEqual(0.5, metrics["precision"])
        self.assertEqual(0.5, metrics["recall"])
        self.assertEqual(1, metrics["coverage_incomplete_cases"])
        self.assertEqual(0, score["by_split"]["train"]["case_count"])

    def test_valid_without_explicit_tp_and_unjudged_controls(self):
        with patch("review_memory.core.load_git_snapshot", return_value=self.semantic_snapshot()):
            prepared = self.prepare()
        report = self.finish_semantic(prepared)
        judgment = {"finding_id": report["findings"][0]["finding_id"], "disposition": "valid",
                    "rationale": "Suggestion acceptable, no confirmed problem-instance match."}
        score = self.score(prepared, self.labels(prepared, [judgment], normal=True))
        metrics = score["by_split"]["test"]
        self.assertEqual(0, metrics["true_positives"])
        self.assertEqual(3, metrics["unjudged"])
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["normal_control_inappropriate_alert_rate"])
        self.assertEqual(1, metrics["normal_control_cases_with_alerts"])

    def test_missing_unfinished_mismatched_and_mutated_reports_rejected(self):
        with patch("review_memory.core.load_git_snapshot", return_value=self.semantic_snapshot()):
            prepared = self.prepare()
        labels = self.labels(prepared)
        with self.assertRaisesRegex(Error, "unfinished"):
            self.score(prepared, labels)
        report = self.finish_semantic(prepared)
        directory = self.run_directory(prepared)
        (directory / "report.json").unlink()
        with self.assertRaises(Error):
            self.score(prepared, labels)
        write_json(directory / "report.json", dict(report, awaiting_response=True))
        with self.assertRaisesRegex(Error, "completion"):
            self.score(prepared, labels)
        write_json(directory / "report.json", report)
        response = load_json(directory / "response.json")
        response["model"]["model"] = "mutated"
        write_json(directory / "response.json", response)
        with self.assertRaisesRegex(Error, "host response"):
            self.score(prepared, labels)
        task = load_json(directory / "task.json")
        task["head_sha"] = self.base
        write_json(directory / "task.json", task)
        with self.assertRaisesRegex(Error, "Frozen"):
            self.score(prepared, labels)

    def test_independent_human_required_and_finding_mapping_unique(self):
        prepared = self.prepare()
        labels = self.labels(prepared)
        labels["adjudicator"]["kind"] = "model"
        with self.assertRaisesRegex(Error, "Independent human"):
            self.score(prepared, labels)
        labels["adjudicator"]["kind"] = "human"
        labels["adjudicator"]["independent"] = False
        with self.assertRaisesRegex(Error, "Independent human"):
            self.score(prepared, labels)
        labels["adjudicator"]["independent"] = True
        judgment = {"finding_id": "finding-1", "disposition": "false_positive", "rationale": "Fixture"}
        labels["cases"][0]["judgments"] = [judgment, judgment]
        with self.assertRaisesRegex(Error, "only one"):
            self.score(prepared, labels)
        labels["cases"][0]["judgments"] = [judgment]
        with self.assertRaisesRegex(Error, "absent"):
            self.score(prepared, labels)

    def test_normal_control_inappropriate_alerts_and_unlabeled_cases(self):
        with patch("review_memory.core.load_git_snapshot", return_value=self.semantic_snapshot()):
            prepared = self.prepare()
        report = self.finish_semantic(prepared)
        judgments = [
            {"finding_id": finding["finding_id"], "disposition": "false_positive",
             "rationale": "Human verified this normal-control alert is inappropriate."}
            for finding in report["findings"]
        ]
        labels = self.labels(prepared, judgments, normal=True)
        metrics = self.score(prepared, labels)["by_split"]["test"]
        self.assertEqual(3, metrics["false_positives"])
        self.assertEqual(0, metrics["precision"])
        self.assertEqual(1, metrics["normal_control_inappropriate_alert_cases"])
        self.assertEqual(1, metrics["normal_control_inappropriate_alert_rate"])
        labels["cases"] = []
        metrics = self.score(prepared, labels)["by_split"]["test"]
        self.assertEqual(1, metrics["unlabeled_case_count"])
        self.assertEqual(3, metrics["unjudged"])
        self.assertIsNone(metrics["precision"])

    def test_frozen_manifest_and_adjudication_time_rejected(self):
        prepared = self.prepare()
        labels = self.labels(prepared)
        labels["cases"][0]["reviewed_at"] = "2025-01-01T00:00:00Z"
        with self.assertRaisesRegex(Error, "precede"):
            self.score(prepared, labels)
        path = Path(prepared["manifest_path"])
        manifest = load_json(path)
        manifest["runs"][0]["case_id"] = "changed"
        write_json(path, manifest)
        with self.assertRaisesRegex(Error, "Frozen replay manifest"):
            self.score(prepared, self.labels(prepared))

    def test_completion_hash_is_verified(self):
        prepared = self.prepare()
        path = self.run_directory(prepared) / "completion.json"
        completion = load_json(path)
        completion["completion_hash"] = "tampered"
        write_json(path, completion)
        with self.assertRaisesRegex(Error, "completion hash"):
            self.score(prepared, self.labels(prepared))


if __name__ == "__main__":
    unittest.main()
