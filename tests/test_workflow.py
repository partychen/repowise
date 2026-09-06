import tempfile
import subprocess
import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_core import knowledge
from review_memory.collect import FIXTURE_SCHEMA, LEGACY_FIXTURE_SCHEMA, _GitHub, _Store, _Unavailable, bootstrap, harvest
from review_memory.common import Error, load_json, load_yaml, write_json, write_yaml
from review_memory.core import approval_request, approve, initialize
from review_memory.propose import propose
from review_memory.review import finalize_review, prepare_review
from review_memory.replay import prepare_replay, score_replay
from review_memory.cli import main


class LearningWorkflowTests(unittest.TestCase):
    def test_missing_version_is_gap_but_rate_limit_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = _GitHub("example/project", _Store(Path(directory), "example/project"))
            missing = subprocess.CompletedProcess([], 1, "", "not found (HTTP 404)")
            with patch("review_memory.collect.subprocess.run", return_value=missing):
                with self.assertRaises(_Unavailable):
                    adapter.content("a" * 40, "src/lib.rs")
            limited = subprocess.CompletedProcess([], 1, "", "rate limit exceeded (HTTP 403) secret-text")
            with patch("review_memory.collect.subprocess.run", return_value=limited):
                with self.assertRaises(Error) as result:
                    adapter.content("a" * 40, "src/lib.rs")
            self.assertIn("rate limited", str(result.exception))
            self.assertNotIn("secret-text", str(result.exception))

    def test_collection_to_proposal_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root, "example/project")
            fixture = root / "fixture.json"
            write_json(fixture, {
                "schema_version": FIXTURE_SCHEMA, "repository": "example/project",
                "prs": [{
                    "number": 7, "created_at": "2026-01-02T00:00:00Z",
                    "merged_at": "2026-01-03T00:00:00Z", "reviews": [],
                    "comments": [{
                        "id": 71, "body": "Synthetic example: preserve caller-visible error context.",
                        "path": "src/lib.rs", "side": "RIGHT", "original_commit_id": "a" * 40,
                        "commit_id": "a" * 40, "original_line": 1,
                        "created_at": "2026-01-02T01:00:00Z", "updated_at": "2026-01-02T01:00:00Z",
                    }], "issue_comments": [],
                }],
            })
            collection = bootstrap(root, "example/project", "2026-01-01", "2026-02-01", fixture=fixture)
            self.assertTrue(collection["complete"])
            legacy_fixture = load_json(fixture)
            legacy_fixture["schema_version"] = LEGACY_FIXTURE_SCHEMA
            write_json(fixture, legacy_fixture)
            self.assertTrue(harvest(root, "example/project", 7, fixture=fixture)["complete"])
            task_path = root / collection["task_paths"][0]
            task = load_json(task_path)
            candidate = knowledge()
            candidate.update(maturity="candidate", sources=[], evidence_ids=[task["evidence"][0]["evidence_id"]])
            response = root / "response.json"
            write_json(response, {
                "task_id": task["task_id"], "input_hash": task["input_hash"],
                "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "test-v1"},
                "candidates": [candidate],
            })
            result = propose(root, task_path, response)
            manifest = load_json(Path(result["proposal"]))
            self.assertEqual(manifest["candidates"][0]["observed_pr_count"], 1)
            self.assertEqual(manifest["candidates"][0]["knowledge"]["sources"][0]["version"],
                             task["evidence"][0]["content_hash"])
            self.assertEqual(harvest(root, "example/project", 7, fixture=fixture)["task_paths"],
                             collection["task_paths"])
            self.assertEqual(propose(root, task_path, response)["status"], "already_proposed")
            evidence_path = root / task["evidence"][0]["path"]
            evidence = load_json(evidence_path)
            evidence["observations"][0]["body"] = "Modified after preparation."
            write_json(evidence_path, evidence)
            with self.assertRaises(Error):
                propose(root, task_path, response)


class SignedReviewWorkflowTests(unittest.TestCase):
    def test_signed_policy_and_detector_review_ignore_pr_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root, "example/project")
            key = root / "ephemeral-test-key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           check=True, capture_output=True)
            (root / ".review" / "allowed_signers").write_text(
                "test-only " + key.with_suffix(".pub").read_text(), encoding="utf-8")
            item = knowledge()
            item.update(execution="hybrid", detector_ref="D-example")
            item["applicability"]["paths"] = ["**/Cargo.toml", "**/*.rs"]
            detector = {
                "schema_version": 1, "id": "D-example", "revision": 1,
                "tool_id": "rust.forbidden-dependency.v1",
                "parameters": {"from_package": "domain", "to_package": "api"},
                "owner": "test-only", "validation_ref": "synthetic:test-fixture",
                "valid_examples": ["No api dependency."], "violating_examples": ["Explicit api dependency."],
            }
            for kind, content in (("knowledge", item), ("detector", detector)):
                content_path = root / f"{kind}.yaml"
                write_yaml(content_path, content)
                request = Path(approval_request(root, content_path, kind, "test-only", "Synthetic regression fixture only")["request"])
                subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "review-memory-v1", str(request)],
                               check=True, capture_output=True)
                approve(root, request, Path(str(request) + ".sig"))

            def git(*args):
                return subprocess.run(["git", "-C", str(root), *args], check=True,
                                      capture_output=True).stdout.decode().strip()

            git("init", "-q")
            git("config", "user.name", "Synthetic test")
            git("config", "user.email", "test@example.invalid")
            git("config", "commit.gpgsign", "false")
            git("config", "core.hooksPath", str(root / "no-hooks"))
            git("config", "core.autocrlf", "false")
            (root / "Cargo.toml").write_text('[package]\nname="domain"\nversion="0.1.0"\n', encoding="utf-8")
            git("add", ".review", "Cargo.toml")
            git("commit", "-qm", "Synthetic approved baseline")
            base = git("rev-parse", "HEAD")
            (root / "Cargo.toml").write_text(
                '[package]\nname="domain"\nversion="0.1.0"\n[dependencies]\napi="1"\n', encoding="utf-8")
            policy_file = root / ".review" / "knowledge" / "K-example-r1.yaml"
            tampered = load_yaml(policy_file)
            tampered["principle"] = "PR tries to relax its own policy."
            write_yaml(policy_file, tampered)
            git("add", ".review", "Cargo.toml")
            git("commit", "-qm", "Synthetic untrusted change")
            head = git("rev-parse", "HEAD")
            prepared = prepare_review(root, "example/project", base, head, base,
                                      reference_query="Rust ownership", reference_limit=1)
            self.assertEqual(1, prepared["report"]["total_findings"])
            self.assertEqual(1, len(prepared["task"]["reference_packs"]))
            self.assertEqual(item["principle"], prepared["task"]["knowledge"][0]["principle"])
            self.assertEqual("D-example", prepared["report"]["findings"][0]["detector_id"])
            response_path = root / "response.json"
            write_json(response_path, {
                "schema_version": 1, "task_id": prepared["task_id"], "input_hash": prepared["input_hash"],
                "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "test-v1"},
                "assessments": [{"knowledge_id": item["id"], "revision": 1, "status": "checked",
                                 "rationale": "Synthetic semantic acknowledgement; no real model result."}],
                "findings": [],
            })
            with patch("review_memory.review.runtime_info", return_value={"hash": "changed"}):
                with self.assertRaisesRegex(Error, "Runtime changed"):
                    finalize_review(root, prepared["run_id"], response_path)
            report = finalize_review(root, prepared["run_id"], response_path)
            self.assertEqual(1, report["total_findings"])
            self.assertFalse(report["awaiting_response"])
            self.assertTrue((Path(prepared["report_path"]).parent / "completion.json").exists())
            now = datetime.now(timezone.utc)
            stamp = lambda delta: (now + timedelta(days=delta)).isoformat()
            dataset = root / "dataset.json"
            write_json(dataset, {
                "schema_version": 1, "timeline": "simulated",
                "windows": {
                    "train": {"start": stamp(-3), "end": stamp(-2)},
                    "dev": {"start": stamp(-2), "end": stamp(-1)},
                    "test": {"start": stamp(-1), "end": stamp(1)},
                },
                "events": [{"id": "synthetic-case", "type": "review", "available_at": stamp(0),
                            "repository": "example/project", "base": base, "head": head, "trusted_ref": base}],
            })
            replay = prepare_replay(root, dataset)
            replay_run = replay["runs"][0]["run_id"]
            replay_directory = Path(replay["runs_root"]) / replay_run
            replay_task = load_json(replay_directory / "task.json")
            self.assertEqual([], replay_task["reference_packs"])
            response = load_json(response_path)
            response.update(task_id=replay_task["task_id"], input_hash=replay_task["input_hash"])
            write_json(response_path, response)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(["--root", str(root), "finalize", "--replay-id", replay["replay_id"],
                                  "--run-id", replay_run, "--response", str(response_path)])
            self.assertEqual(exit_code, 2)  # Semantic context remains explicitly incomplete.
            replay_report = load_json(replay_directory / "report.json")
            labels_path = root / "labels.json"
            write_json(labels_path, {
                "schema_version": 1, "replay_id": replay["replay_id"],
                "adjudicator": {"identity": "synthetic-human-attestation", "kind": "human", "independent": True},
                "cases": [{"case_id": "synthetic-case", "reviewed_at": datetime.now(timezone.utc).isoformat(),
                           "rationale": "Synthetic test label, not a product accuracy measurement.",
                           "normal_control": False, "issues": [{"id": "edge-1"}],
                           "judgments": [{"finding_id": replay_report["findings"][0]["finding_id"],
                                          "disposition": "valid", "true_positive": True, "issue_id": "edge-1",
                                          "rationale": "Parsed synthetic forbidden edge."}]}],
            })
            score = score_replay(root, replay["replay_id"], labels_path)
            self.assertEqual(1, score["by_split"]["test"]["precision_denominator"])
            self.assertEqual(1, score["by_split"]["test"]["matched_problem_instances"])
            self.assertEqual(score, score_replay(root, replay["replay_id"], labels_path))
            self.assertEqual(report, load_json(Path(prepared["report_path"])))


if __name__ == "__main__":
    unittest.main()
