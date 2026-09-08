import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_core import knowledge
from repowise import collect
from repowise.cli import main
from repowise.common import Error, load_json, write_json
from repowise.propose import propose
from repowise.sync import project_status, sync_project


class SyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture.json"
        self.pr = {"number": 1, "created_at": "2025-01-01T00:00:00Z",
                   "merged_at": "2026-01-03T00:00:00Z",
                   "reviews": [{"id": 21, "body": "Validate boundary input before using it.",
                                "state": "CHANGES_REQUESTED"}],
                   "comments": [], "issue_comments": [], "versions": []}
        self.save([self.pr])

    def save(self, prs):
        write_json(self.fixture, {"schema_version": collect.FIXTURE_SCHEMA,
                                  "repository": "example/project", "prs": prs})

    def sync(self, **kwargs):
        return sync_project(self.root, "example/project", fixture=self.fixture, **kwargs)

    def propose_task(self, reference, empty=False):
        task_path = self.root / reference["path"]
        task = load_json(task_path)
        candidate = knowledge()
        candidate.update(maturity="candidate", sources=[],
                         evidence_ids=[task["evidence"][0]["evidence_id"]])
        response = {"task_id": task["task_id"], "input_hash": task["input_hash"],
                    "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "test-v1"},
                    "candidates": [] if empty else [candidate]}
        response_path = self.root / "response.json"
        write_json(response_path, response)
        return propose(self.root, task_path, response_path)

    def test_connect_sync_learn_then_incremental_update(self):
        first = self.sync()
        self.assertTrue(first["collection_complete"])
        self.assertFalse(first["learning_complete"])
        self.assertEqual(first["pending_task_count"], 1)
        self.propose_task(first["pending_tasks"][0])
        status = project_status(self.root, offline=True)
        self.assertTrue(status["learning_complete"])
        self.assertEqual(status["knowledge_count"], 1)
        self.assertEqual(self.sync()["prs"], [])
        updated = copy.deepcopy(self.pr)
        updated["reviews"][0]["body"] = "Validate both boundaries before using the input."
        self.save([updated, {**self.pr, "number": 2}])
        second = self.sync()
        self.assertEqual(set(second["prs"]), {1, 2})
        for task in second["pending_tasks"]:
            self.propose_task(task)
        status = project_status(self.root, offline=True)
        self.assertEqual(status["knowledge_count"], 1)
        self.assertEqual(status["completed_task_count"], 3)
        library = load_json(self.root / status["knowledge_path"])
        self.assertEqual(set(library["entries"][0]["prs"]), {1, 2})
        self.assertEqual(library["authority"], "unapproved")
        self.assertFalse(list((self.root / ".review" / "knowledge").glob("*.yaml")))

    def test_batch_resumes_without_recollecting_or_skipping_failures(self):
        self.save([{**self.pr, "number": i} for i in (1, 2, 3)])
        first = self.sync(max_prs=1)
        self.assertEqual(first["remaining_pr_count"], 2)
        self.assertFalse(first["complete"])
        second = self.sync(max_prs=1)
        self.assertNotEqual(first["prs"], second["prs"])
        with patch.object(collect._Fixture, "feedback", side_effect=Error("read failed")):
            failed = self.sync(max_prs=1)
        self.assertFalse(failed["complete"])
        self.assertEqual(failed["remaining_pr_count"], 1)
        self.assertEqual(project_status(self.root, offline=True)["collected_pr_count"], 2)
        final = self.sync(max_prs=1)
        self.assertTrue(final["complete"])
        self.assertEqual(set(first["prs"] + second["prs"] + final["prs"]), {1, 2, 3})

    def test_empty_host_result_completes_task_without_inventing_knowledge(self):
        first = self.sync()
        self.propose_task(first["pending_tasks"][0], empty=True)
        status = project_status(self.root, offline=True)
        self.assertTrue(status["learning_complete"])
        self.assertEqual(status["knowledge_count"], 0)

    def test_since_is_merge_time_and_fixture_state_is_separate(self):
        first = self.sync(since="2026-01-01")
        self.assertEqual(first["prs"], [1])
        self.assertEqual(project_status(self.root)["pending_task_count"], 0)
        with self.assertRaisesRegex(Error, "history scope"):
            self.sync(since="2024-01-01")

    def test_refresh_rechecks_feedback_without_duplicate_tasks(self):
        first = self.sync()
        refreshed = self.sync(refresh=True)
        self.assertEqual(refreshed["prs"], [1])
        self.assertEqual(first["pending_tasks"], refreshed["pending_tasks"])

    def test_discovery_failure_cannot_advance_or_claim_complete(self):
        with patch.object(collect._Fixture, "prs", side_effect=Error("page failed")):
            failed = self.sync()
        self.assertFalse(failed["complete"])
        self.assertFalse(project_status(self.root, offline=True)["collection_complete"])
        self.assertTrue(self.sync()["complete"])

    def test_saved_candidate_tampering_is_not_accepted(self):
        first = self.sync()
        result = self.propose_task(first["pending_tasks"][0])
        path = Path(result["proposal"])
        manifest = load_json(path)
        manifest["candidates"][0]["knowledge"]["sources"][0]["version"] = "forged"
        write_json(path, manifest)
        status = project_status(self.root, offline=True)
        self.assertFalse(status["learning_complete"])
        self.assertEqual(status["pending_task_count"], 1)
        self.assertEqual(status["learning_gap_count"], 1)
        self.assertIn("derivation", status["learning_gaps"][0]["reason"])

    def test_saved_evidence_tampering_does_not_complete_learning(self):
        first = self.sync()
        result = self.propose_task(first["pending_tasks"][0])
        task = load_json(Path(result["proposal"]).parent / "task.json")
        evidence_path = self.root / task["evidence"][0]["path"]
        evidence = load_json(evidence_path)
        evidence["pr"] = 99
        write_json(evidence_path, evidence)
        status = project_status(self.root, offline=True)
        self.assertFalse(status["learning_complete"])
        self.assertEqual(status["pending_task_count"], 0)
        self.assertEqual(status["learning_gap_count"], 1)
        self.assertIn("changed after collection", status["learning_gaps"][0]["reason"])

    def test_cli_connects_without_separate_init_and_remembers_repository(self):
        with tempfile.TemporaryDirectory() as data_home:
            arguments = ["--root", str(self.root), "--data-home", data_home]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([*arguments, "sync", "--repository", "Example/Project",
                             "--fixture", str(self.fixture)])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["pending_task_count"], 1)
            storage = Path(result["storage_root"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*arguments, "sync", "--fixture", str(self.fixture)]), 0)
            with self.assertRaises(Error):
                sync_project(storage, "another/repo", fixture=self.fixture)
            self.assertFalse((self.root / ".review").exists())

    def test_live_incremental_metadata_and_explicit_feedback_refresh(self):
        meta = collect._Fixture._metadata(self.pr)
        feedback = copy.deepcopy(self.pr)
        with patch.object(collect._GitHub, "pages", side_effect=lambda endpoint: iter([dict(meta)])), \
                patch.object(collect._GitHub, "pr", side_effect=lambda number: dict(meta)), \
                patch.object(collect._GitHub, "feedback", side_effect=lambda number, kind: feedback[kind]):
            first = sync_project(self.root, "example/project")
            self.assertEqual(first["prs"], [1])
            self.assertFalse(first["offline"])
            feedback["reviews"][0]["body"] = "A new comment without changed PR listing metadata."
            self.assertEqual(sync_project(self.root)["prs"], [])
            refreshed = sync_project(self.root, refresh=True)
            self.assertEqual(refreshed["prs"], [1])
            self.assertEqual(refreshed["pending_task_count"], 2)
            meta["updated_at"] = "2026-02-01T00:00:00Z"
            self.assertEqual(sync_project(self.root)["prs"], [1])

    def test_bad_budget_does_not_initialize_project(self):
        for budget in (0, 101, True):
            with self.subTest(budget=budget), self.assertRaises(Error):
                self.sync(max_prs=budget)
        self.assertFalse((self.root / ".review" / "config.yaml").exists())


if __name__ == "__main__":
    unittest.main()
