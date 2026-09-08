"""Synthetic authorized-host handoffs; the runtime never edits or executes the target."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.test_core import file_tree, fixture_directory, git_command, initialize_git, knowledge
from review_memory.common import Error, load_json, write_json
from review_memory.core import DEFAULT_CONFIG, initialize
from review_memory.feature import finish_feature, prepare_feature
from review_memory.review_contract import REPOSITORY_DIMENSIONS


class FeatureTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(fixture_directory())
        self.target, self.memory = directory / "code", directory / "memory"
        initialize_git(self.target)
        initialize(self.memory, "example/project")
        (self.target / "src").mkdir()
        (self.target / "src" / "service.rs").write_bytes(b"pub fn existing() {}\n")
        (self.target / "src" / "helper.rs").write_bytes(b"pub fn helper() {}\n")
        git_command(self.target, "add", ".")
        git_command(self.target, "commit", "-qm", "Synthetic feature baseline")
        self.base = git_command(self.target, "rev-parse", "HEAD")
        self.item = knowledge()
        self.snapshot = {
            "hash": "synthetic-snapshot", "trusted_sha": "c" * 40,
            "config": dict(DEFAULT_CONFIG, repository="example/project"),
            "knowledge": [self.item], "manifest": {"coverage_gaps": []},
        }
        self.loader = self.enterContext(patch("review_memory.feature.load_git_snapshot",
                                              return_value=self.snapshot))
        self.enterContext(patch("review_memory.feature.runtime_info",
                                return_value={"hash": "synthetic-stable-runtime"}))

    def prepare(self, **options):
        return prepare_feature(
            self.target, "example/project", "Implement the requested option using existing project helpers.",
            memory_root=self.memory, context_paths=["src/service.rs"],
            trusted_ref=options.pop("trusted_ref", "policy"), **options,
        )

    def response(self, prepared):
        task = prepared["task"]
        reference = {"snapshot": "base", "commit": self.base, "path": "src/service.rs",
                     "line_start": 1, "line_end": 1, "text": "pub fn existing() {}"}
        return {
            "schema_version": 1, "task_id": task["task_id"], "input_hash": task["input_hash"],
            "model": {"provider": "host", "model": "synthetic", "prompt_version": "synthetic-feature"},
            "status": "completed", "summary": "Synthetic host record, not an actual feature result.",
            "changes": [{"path": "src/service.rs", "summary": "Synthetic host change description."}],
            "checks": [{"command": "synthetic-check-do-not-execute", "outcome": "passed",
                        "summary": "Synthetic result used to test recording, not real execution."}],
            "knowledge_assessments": [
                {"knowledge_id": self.item["id"], "revision": self.item["revision"],
                 "status": "applied", "rationale": "Synthetic conformance record."}
            ],
            "repository_assessments": [
                {"dimension": dimension, "status": "checked", "references": [reference],
                 "rationale": "Synthetic protocol exercise, not independent assessment."}
                for dimension in REPOSITORY_DIMENSIONS
            ],
            "remaining_work": [],
        }

    def finish(self, prepared, response):
        path = self.memory / ".review" / "local" / "host-feature-response.json"
        write_json(path, response)
        return finish_feature(self.memory, prepared["feature_id"], path)

    def test_feature_context_is_pinned_and_does_not_grant_authority(self):
        (self.target / "src" / "service.rs").write_text("dirty user content\n")
        before = file_tree(self.target)
        prepared = self.prepare()
        self.assertFalse(prepared["implemented"])
        self.assertEqual("awaiting_authorized_host_implementation", prepared["stage"])
        self.assertIn("authorization required", prepared["task"]["authorization"]["target_edits"])
        self.assertEqual(self.base, prepared["task"]["base_sha"])
        self.assertEqual([self.item], prepared["task"]["knowledge"])
        contexts = {item["path"]: item for item in prepared["task"]["code_context"]}
        self.assertEqual("pub fn existing() {}\n", contexts["src/service.rs"]["before"])
        self.assertIn("src/helper.rs", contexts)
        self.assertEqual(before, file_tree(self.target))
        self.assertTrue(Path(prepared["preview_path"]).is_file())
        self.assertFalse((self.target / ".review").exists())

    def test_missing_policy_remains_a_gap_not_an_implicit_approval(self):
        prepared = self.prepare(trusted_ref=None)
        self.assertEqual("incomplete", prepared["status"])
        self.assertEqual([], prepared["task"]["knowledge"])
        self.assertIsNone(prepared["task"]["trusted_sha"])
        self.loader.assert_not_called()

    def test_completion_records_but_does_not_execute_or_edit(self):
        prepared = self.prepare()
        before = file_tree(self.target)
        with patch("review_memory.common.subprocess.run", side_effect=AssertionError("No target execution")):
            result = self.finish(prepared, self.response(prepared))
        self.assertEqual("complete", result["status"])
        self.assertEqual("host_reported_not_independently_verified", result["verification"])
        self.assertEqual(before, file_tree(self.target))
        self.assertEqual(result, self.finish(prepared, self.response(prepared)))
        self.assertIn("host-reported", Path(result["preview_path"]).read_text())

    def test_failed_checks_or_unresolved_work_cannot_be_completed(self):
        prepared = self.prepare()
        response = self.response(prepared)
        response["checks"][0]["outcome"] = "failed"
        with self.assertRaisesRegex(Error, "cannot retain failed"):
            self.finish(prepared, response)
        response["checks"][0]["outcome"] = "passed"
        response["remaining_work"] = ["Incomplete caller wiring"]
        with self.assertRaisesRegex(Error, "unresolved work"):
            self.finish(prepared, response)
        response["status"] = "blocked"
        self.assertEqual("incomplete", self.finish(prepared, response)["status"])

    def test_missing_checks_and_assessments_remain_visible(self):
        prepared = self.prepare()
        response = self.response(prepared)
        response.update(checks=[], knowledge_assessments=[], repository_assessments=[])
        result = self.finish(prepared, response)
        self.assertEqual("incomplete", result["status"])
        self.assertIn("No host verification checks were recorded.", result["coverage_gaps"])
        self.assertTrue(any("Missing feature knowledge assessment" in gap for gap in result["coverage_gaps"]))
        self.assertIn("Missing repository assessment: idioms", result["coverage_gaps"])

    def test_response_cannot_grant_permission_or_invent_policy(self):
        prepared = self.prepare()
        variants = []
        response = self.response(prepared)
        response["authorization"] = {"target_edits": True}
        variants.append(response)
        response = self.response(prepared)
        response["knowledge_assessments"][0]["knowledge_id"] = "K-unapproved"
        variants.append(response)
        for path in ("../outside", "C:/outside", ".git/config"):
            response = self.response(prepared)
            response["changes"][0]["path"] = path
            variants.append(response)
        response = self.response(prepared)
        response["repository_assessments"][0]["references"][0]["text"] = "invented"
        variants.append(response)
        for response in variants:
            with self.subTest(response=response), self.assertRaises(Error):
                self.finish(prepared, response)

    def test_task_and_record_tampering_are_rejected(self):
        prepared = self.prepare()
        response = self.response(prepared)
        result = self.finish(prepared, response)
        stored = load_json(Path(result["report_path"]))
        stored["status"] = "invented"
        write_json(Path(result["report_path"]), stored)
        with self.assertRaisesRegex(Error, "different completion"):
            self.finish(prepared, response)
        task = load_json(Path(prepared["task_path"]))
        task["goal"] = "Changed after handoff"
        write_json(Path(prepared["task_path"]), task)
        with self.assertRaisesRegex(Error, "modified"):
            self.finish(prepared, response)


if __name__ == "__main__":
    unittest.main()
