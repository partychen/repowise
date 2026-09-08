"""Collection fixtures are generated in project-local scratch directories."""

import importlib
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "repowise" / "scripts"))
collect = importlib.import_module("repowise.collect")


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "tests" / "fixtures" / "collection" / ("run-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        (self.root / ".review" / "local").mkdir(parents=True)
        self.fixture = self.root / "fixture.json"
        self.pr = {
            "number": 1, "created_at": "2026-01-05T00:00:00Z",
            "merged_at": "2026-01-06T00:00:00Z",
            "reviews": [{"id": 30, "state": "CHANGES_REQUESTED", "body": "Please validate."}],
            "comments": [
                {"id": 10, "body": "Check the bound.", "path": "a.py", "side": "RIGHT",
                 "original_commit_id": "a" * 40, "commit_id": "b" * 40,
                 "line": 2, "original_line": 1, "updated_at": "2026-01-05T01:00:00Z"},
                {"id": 11, "body": "Fixed", "in_reply_to_id": 10, "path": "other.py",
                 "updated_at": "2026-01-05T02:00:00Z"},
                {"id": 12, "body": "Different thread at same location.", "path": "a.py",
                 "side": "RIGHT", "original_commit_id": "a" * 40, "line": 2,
                 "updated_at": "2026-01-05T03:00:00Z"}],
            "issue_comments": [{"id": 40, "body": "General discussion."}],
            "versions": [
                {"comment_id": 10, "comment_updated_at": "2026-01-05T01:00:00Z",
                 "before": {"commit": "a" * 40, "path": "a.py", "side": "RIGHT", "text": "x = 1"},
                 "after": {"commit": "b" * 40, "path": "a.py", "side": "RIGHT", "text": "x = 2"}}],
        }
        self.save()

    def tearDown(self):
        shutil.rmtree(self.root)

    def save(self, prs=None):
        self.fixture.write_text(json.dumps({
            "schema_version": collect.FIXTURE_SCHEMA, "repository": "acme/repo",
            "prs": [self.pr] if prs is None else prs}), encoding="utf-8")

    def harvest(self):
        return collect.harvest(self.root, "acme/repo", 1, fixture=self.fixture)

    def load(self, relative):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def bundles(self, result):
        return [self.load(item["path"]) for item in result["prs"][0]["evidence"]]

    def test_replies_follow_ids_not_locations_and_fixed_is_not_implemented(self):
        result = self.harvest()
        self.assertTrue(result["complete"])
        bundles = self.bundles(result)
        threads = [item for item in bundles if item["kind"] == "review_thread"]
        self.assertEqual(len(threads), 2)
        thread = next(item for item in threads if item["root_comment_id"] == 10)
        self.assertEqual([item["id"] for item in thread["observations"]], [10, 11])
        self.assertEqual(thread["before"]["commit"], "a" * 40)
        self.assertEqual(thread["after"]["availability"], "available")
        self.assertEqual(thread["response_outcome"]["status"], "ambiguous")
        self.assertTrue(thread["observations"][1]["claimed_fixed"])
        self.assertIsNone(thread["response_outcome"]["implemented"])
        self.assertTrue(all(item["response_outcome"]["status"] in {"ambiguous", "unavailable"}
                            for item in bundles))
        self.assertEqual({x["kind"] for x in bundles},
                         {"review_thread", "review", "issue_comment"})

    def test_repeated_harvest_is_idempotent_and_edit_is_a_new_version(self):
        first = self.harvest()
        first_task = (self.root / first["task_paths"][0]).read_bytes()
        second = self.harvest()
        self.assertEqual(first["task_paths"], second["task_paths"])
        self.assertEqual(first_task, (self.root / second["task_paths"][0]).read_bytes())
        self.assertEqual(first["prs"][0]["evidence"], second["prs"][0]["evidence"])
        for reference in second["prs"][0]["evidence"]:
            bundle = self.load(reference["path"])
            self.assertEqual(reference["content_hash"], collect.digest(bundle))
            self.assertEqual(reference["available_at"], bundle["timestamps"]["observed_at"])
        self.pr["comments"][0]["body"] = "Check both bounds."
        self.pr["comments"][0]["updated_at"] = "2026-01-07T00:00:00Z"
        self.save()
        third = self.harvest()
        self.assertNotEqual(first["task_paths"], third["task_paths"])
        thread = next(x for x in self.bundles(third) if x["root_comment_id"] == 10)
        self.assertEqual(thread["after"]["availability"], "unavailable")
        self.assertTrue((self.root / first["task_paths"][0]).exists())

    def test_bootstrap_creation_window_and_budget_do_not_advance_watermark(self):
        self.save([self.pr, {**self.pr, "number": 2, "created_at": "2026-01-08T00:00:00Z"},
                   {**self.pr, "number": 3, "created_at": "2026-01-09T00:00:00Z",
                    "merged_at": None},
                   {**self.pr, "number": 4, "created_at": "2025-12-01T00:00:00Z"}])
        result = collect.bootstrap(self.root, "acme/repo", "2026-01-01", "2026-02-01",
                                   max_prs=1, fixture=self.fixture)
        self.assertFalse(result["complete"])
        self.assertTrue(result["truncated"])
        self.assertFalse(result["state_advanced"])
        self.assertEqual([x["pr"] for x in result["prs"]], [2])
        self.assertFalse((self.root / ".review" / "local" / "state" / "collection.json").exists())
        result = collect.bootstrap(self.root, "acme/repo", "2026-01-01", "2026-02-01",
                                   max_prs=2, fixture=self.fixture)
        self.assertTrue(result["complete"])
        self.assertEqual([x["pr"] for x in result["prs"]], [2, 1])

    def test_partial_feedback_failure_preserves_raw_run_and_old_state(self):
        self.harvest()
        state = self.root / ".review" / "local" / "state" / "collection.json"
        prior = state.read_bytes()
        del self.pr["issue_comments"]
        self.save()
        result = self.harvest()
        self.assertFalse(result["complete"])
        self.assertFalse(result["state_advanced"])
        self.assertTrue(result["failures"])
        self.assertTrue(result["raw_paths"])
        self.assertEqual(prior, state.read_bytes())
        self.assertEqual(self.load(result["run_path"])["status"], "partial")

    def test_unmerged_harvest_is_unavailable_and_preserves_success_watermark(self):
        self.harvest()
        state = self.root / ".review" / "local" / "state" / "collection.json"
        prior = state.read_bytes()
        self.pr["merged_at"] = None
        self.pr["state"] = "open"
        self.save()
        with patch.object(collect._Fixture, "feedback") as feedback:
            result = self.harvest()
        feedback.assert_not_called()
        self.assertTrue(result["policy"]["merged_only"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["state_advanced"])
        self.assertEqual(result["task_paths"], [])
        self.assertIn("not merged", result["failures"][0]["error"])
        self.assertTrue(result["raw_paths"])
        self.assertEqual(prior, state.read_bytes())

    def test_metadata_number_is_validated_before_and_after_collection(self):
        good = {key: value for key, value in self.pr.items()
                if key not in {"reviews", "comments", "issue_comments", "versions"}}
        wrong = {**good, "number": 2}
        with patch.object(collect._Fixture, "pr", return_value=wrong):
            with patch.object(collect._Fixture, "feedback") as feedback:
                result = self.harvest()
        feedback.assert_not_called()
        self.assertFalse(result["complete"])
        self.assertIn("does not match", result["failures"][0]["error"])
        for final in [wrong, {**good, "merged_at": None}, {**good, "title": "Changed"}]:
            with self.subTest(final=final):
                with patch.object(collect._Fixture, "pr", side_effect=[good, final]):
                    result = self.harvest()
                self.assertFalse(result["complete"])
                self.assertFalse(result["state_advanced"])
                self.assertEqual(result["task_paths"], [])
                self.assertTrue(result["failures"])
        self.assertFalse((self.root / ".review" / "local" / "state" / "collection.json").exists())

    def test_altered_stored_bundle_is_not_reendorsed_with_a_new_hash(self):
        first = self.harvest()
        reference = first["prs"][0]["evidence"][0]
        path = self.root / reference["path"]
        stored = self.load(reference["path"])
        stored["observations"][0]["body"] = "Altered evidence"
        path.write_text(json.dumps(stored), encoding="utf-8")
        result = self.harvest()
        self.assertFalse(result["complete"])
        self.assertFalse(result["state_advanced"])
        self.assertIn("content identity", result["failures"][0]["error"])

    def test_ambiguous_versions_never_choose_an_after(self):
        self.pr["versions"].append({
            **self.pr["versions"][0],
            "after": {"commit": "c" * 40, "path": "a.py", "side": "RIGHT", "text": "other"}})
        self.save()
        thread = next(x for x in self.bundles(self.harvest()) if x["root_comment_id"] == 10)
        self.assertEqual(thread["before"]["availability"], "unavailable")
        self.assertEqual(thread["after"]["availability"], "unavailable")
        self.assertEqual(thread["response_outcome"]["status"], "unavailable")
        self.assertIn("conflicting", thread["after"]["reason"])

    def test_left_side_does_not_use_head_commit(self):
        self.pr["versions"] = []
        self.pr["comments"][0]["side"] = "LEFT"
        self.save()
        with patch.object(collect._Fixture, "content", side_effect=AssertionError("wrong ref")):
            self.pr["comments"] = self.pr["comments"][:2]
            self.save()
            thread = self.bundles(self.harvest())[0]
        self.assertEqual(thread["before"]["availability"], "unavailable")
        self.assertIn("LEFT", thread["before"]["reason"])

    def test_invalid_repository_and_time_bounds(self):
        for repo in ["https://github.com/acme/repo", "../repo", "acme/..", "acme/repo/extra"]:
            with self.assertRaises(collect.Error):
                collect.harvest(self.root, repo, 1, fixture=self.fixture)
        with self.assertRaises(collect.Error):
            collect.bootstrap(self.root, "acme/repo", "2026-02-01", "2026-01-01",
                              fixture=self.fixture)

    def test_multi_page_adapter_and_separate_endpoints(self):
        store = collect._Store(self.root, "acme/repo")
        adapter = collect._GitHub("acme/repo", store)
        calls = []

        def request(command, **kwargs):
            endpoint = command[-1]
            calls.append(endpoint)
            if "/pulls/1/comments?" in endpoint and endpoint.endswith("&page=1"):
                body = [{"id": i} for i in range(1, 101)]
            elif "/pulls/1/comments?" in endpoint:
                body = [{"id": 101}]
            else:
                body = []
            self.assertEqual(command[command.index("--method") + 1], "GET")
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")

        with patch.object(collect.subprocess, "run", side_effect=request):
            self.assertEqual(len(adapter.feedback(1, "comments")), 101)
            adapter.feedback(1, "reviews")
            adapter.feedback(1, "issue_comments")
        self.assertEqual(len(calls), 4)
        self.assertTrue(any("/pulls/1/reviews?" in x for x in calls))
        self.assertTrue(any("/issues/1/comments?" in x for x in calls))
        self.assertEqual(len(store.raw), 4)

    def test_page_failure_and_safety_limit_are_not_success(self):
        store = collect._Store(self.root, "acme/repo")
        adapter = collect._GitHub("acme/repo", store)
        full = [{"id": i} for i in range(1, 101)]
        ok = subprocess.CompletedProcess([], 0, json.dumps(full), "")
        fail = subprocess.CompletedProcess([], 1, "", "credential-secret")
        with patch.object(collect.subprocess, "run", side_effect=[ok, fail]):
            with self.assertRaisesRegex(collect.Error, "exit 1"):
                adapter.feedback(1, "comments")
        self.assertEqual(len(store.raw), 1)
        self.assertNotIn("credential-secret", json.dumps(self.load(store.raw[0])))
        with patch.object(collect, "MAX_PAGES", 1):
            with patch.object(collect.subprocess, "run", return_value=ok):
                with self.assertRaisesRegex(collect.Error, "safety limit"):
                    adapter.feedback(1, "comments")

    def test_immutable_content_uses_original_commit_and_records_source(self):
        self.pr["comments"] = [self.pr["comments"][0]]
        self.pr["versions"] = []
        calls = []

        def request(command, **kwargs):
            endpoint = command[-1]
            calls.append(endpoint)
            if "/contents/" in endpoint:
                body = {"type": "file", "encoding": "base64", "size": 5,
                        "content": base64.b64encode(b"x = 1").decode("ascii")}
            elif endpoint.endswith("/pulls/1"):
                body = {key: value for key, value in self.pr.items()
                        if key not in {"reviews", "comments", "issue_comments", "versions"}}
            elif "/pulls/1/comments?" in endpoint:
                body = self.pr["comments"]
            else:
                body = []
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")

        with patch.object(collect.subprocess, "run", side_effect=request):
            result = collect.harvest(self.root, "acme/repo", 1)
        self.assertTrue(result["complete"])
        thread = self.bundles(result)[0]
        self.assertEqual(thread["before"]["commit"], "a" * 40)
        self.assertEqual(thread["before"]["text"], "x = 1")
        self.assertEqual(thread["after"]["availability"], "unavailable")
        self.assertIsNone(thread["response_outcome"]["implemented"])
        self.assertTrue(any(x.endswith("?ref=" + "a" * 40) for x in calls))
        self.assertFalse(any("HEAD" in x for x in calls))
        self.assertTrue(any("/contents/" in x["endpoint"] for x in thread["sources"]))

    def test_content_transport_failure_is_partial_not_a_successful_unknown(self):
        with patch.object(collect._GitHub, "pr", return_value=self.pr):
            with patch.object(collect._GitHub, "feedback",
                              side_effect=[self.pr["reviews"], self.pr["comments"], []]):
                with patch.object(collect._GitHub, "content",
                                  side_effect=collect.Error("GitHub read failed")):
                    result = collect.harvest(self.root, "acme/repo", 1)
        self.assertFalse(result["complete"])
        self.assertFalse(result["state_advanced"])
        self.assertIn("GitHub read failed", result["failures"][0]["error"])

    def test_learning_task_has_stable_bound_contract_and_evidence(self):
        result = self.harvest()
        task = self.load(result["task_paths"][0])
        self.assertEqual(task["task_type"], "induce")
        self.assertEqual(task["repository"], "acme/repo")
        self.assertEqual(task["input"]["evidence"], task["evidence"])
        self.assertEqual(task["input_hash"], collect.digest(task["input"]))
        self.assertEqual(task["task_id"], "induce-" + task["input_hash"])
        self.assertEqual(task["model_result_contract"]["task_id"], task["task_id"])
        self.assertEqual(task["model_result_contract"]["input_hash"], task["input_hash"])
        contract = task["model_result_contract"]
        self.assertEqual(contract["required"], ["task_id", "input_hash", "model", "candidates"])
        self.assertEqual(contract["model"]["required"], ["provider", "model", "prompt_version"])
        self.assertEqual(contract["candidates"]["required"], ["evidence_ids"])
        self.assertEqual(contract["candidates"]["allowed_maturity"], ["candidate", "lesson"])
        self.assertNotIn("proposals", contract)
        for item in task["evidence"]:
            bundle = self.load(item["path"])
            self.assertEqual(bundle["evidence_id"], item["evidence_id"])
            self.assertEqual(item["content_hash"], collect.digest(bundle))
            self.assertEqual(item["available_at"], bundle["timestamps"]["observed_at"])
            self.assertEqual(item["repository"], bundle["repository"])
            self.assertEqual(item["pr"], bundle["pr"])

    def test_invalid_state_is_preserved_and_run_records_failure(self):
        state = self.root / ".review" / "local" / "state" / "collection.json"
        state.parent.mkdir()
        state.write_text('{"repositories": []}', encoding="utf-8")
        result = self.harvest()
        self.assertFalse(result["complete"])
        self.assertFalse(result["state_advanced"])
        self.assertEqual(state.read_text(encoding="utf-8"), '{"repositories": []}')
        self.assertEqual(self.load(result["run_path"])["status"], "partial")
        self.assertEqual(result["failures"][0]["stage"], "state")

    def test_cycles_fail_and_missing_parent_is_an_explicit_gap(self):
        self.pr["comments"][0]["in_reply_to_id"] = 11
        self.save()
        self.assertFalse(self.harvest()["complete"])
        self.pr["comments"] = [self.pr["comments"][1]]
        self.save()
        result = self.harvest()
        self.assertTrue(result["complete"])
        self.assertTrue(any("unavailable parent 10" in gap for gap in result["gaps"]))
        thread = next(x for x in self.bundles(result) if x["kind"] == "review_thread")
        self.assertEqual(thread["root_comment_id"], 10)
        self.assertIsNone(thread["comment_version"])


if __name__ == "__main__":
    unittest.main()
