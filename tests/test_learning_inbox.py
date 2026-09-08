import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_core import fixture_directory, knowledge
from repowise import collect
from repowise.common import load_json, write_json
from repowise.core import initialize
from repowise.propose import propose
from repowise.sync import project_status


class LearningInboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(fixture_directory())
        self.root = self.directory / "policy"
        initialize(self.root, "example/project")
        self.fixture = self.root / "fixture.json"
        self.pr = {
            "number": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-03T00:00:00Z",
            "reviews": [{"id": 21, "body": "Validate boundary input before using it.",
                         "state": "CHANGES_REQUESTED"}],
            "comments": [],
            "issue_comments": [],
            "versions": [],
        }
        self.save([self.pr])

    def save(self, prs):
        write_json(self.fixture, {
            "schema_version": collect.FIXTURE_SCHEMA,
            "repository": "example/project",
            "prs": prs,
        })

    def harvest(self, pr=1, *, fixture=True):
        return collect.harvest(self.root, "example/project", pr,
                               fixture=self.fixture if fixture else None)

    def bootstrap(self, since="2026-01-01", until="2026-01-31", max_prs=50):
        return collect.bootstrap(self.root, "example/project", since, until,
                                 max_prs=max_prs, fixture=self.fixture)

    def candidate(self, *, identifier="K-boundary", revision=1, title="Boundary validation",
                  principle="Validate boundary input before use.", exceptions=None):
        item = knowledge()
        item.update(
            id=identifier,
            revision=revision,
            title=title,
            principle=principle,
            maturity="candidate",
            sources=[],
            exceptions=[] if exceptions is None else exceptions,
        )
        return item

    def propose_task(self, reference, *, candidate=None, empty=False):
        task_path = self.root / reference["path"]
        task = load_json(task_path)
        response = {
            "task_id": task["task_id"],
            "input_hash": task["input_hash"],
            "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "test-v1"},
            "candidates": [],
        }
        if not empty:
            item = self.candidate() if candidate is None else copy.deepcopy(candidate)
            item["evidence_ids"] = [entry["evidence_id"] for entry in task["evidence"]]
            response["candidates"] = [item]
        response_path = self.root / "response.json"
        write_json(response_path, response)
        return propose(self.root, task_path, response_path)

    def test_direct_harvest_appears_in_status_and_knowledge_without_sync(self):
        harvested = self.harvest()
        status = project_status(self.root, offline=True)
        self.assertFalse(status["collection_complete"])
        self.assertEqual(status["collected_pr_count"], 0)
        self.assertEqual(status["learning_pr_count"], 1)
        self.assertEqual(status["pending_task_count"], 1)
        self.assertEqual(status["pending_tasks"][0]["path"], harvested["task_paths"][0])
        self.propose_task(status["pending_tasks"][0])
        learned = project_status(self.root, offline=True)
        self.assertTrue(learned["learning_complete"])
        self.assertEqual(learned["proposal_count"], 1)
        self.assertEqual(learned["completed_task_count"], 1)
        self.assertEqual(learned["knowledge_count"], 1)
        library = load_json(self.root / learned["knowledge_path"])
        self.assertTrue(library["offline"])
        self.assertEqual(library["completed_task_ids"], [status["pending_tasks"][0]["task_id"]])
        self.assertEqual(library["coverage_gaps"], [])

    def test_bootstrap_tasks_fill_the_learning_inbox_without_sync(self):
        self.save([
            {**self.pr, "number": 1, "created_at": "2026-01-01T00:00:00Z"},
            {**self.pr, "number": 2, "created_at": "2026-01-02T00:00:00Z"},
            {**self.pr, "number": 3, "created_at": "2026-01-03T00:00:00Z"},
        ])
        result = self.bootstrap(until="2026-01-04")
        self.assertTrue(result["complete"])
        paged = project_status(self.root, offline=True, limit=2)
        self.assertFalse(paged["collection_complete"])
        self.assertEqual(paged["learning_pr_count"], 3)
        self.assertEqual(paged["pending_task_count"], 3)
        self.assertEqual(len(paged["pending_tasks"]), 2)
        self.assertEqual(paged["pending_tasks_omitted"], 1)
        full = project_status(self.root, offline=True, limit=10)
        self.assertEqual({item["pr"] for item in full["pending_tasks"]}, {1, 2, 3})

    def test_repeated_harvest_same_input_keeps_one_pending_task(self):
        first = self.harvest()
        second = self.harvest()
        self.assertEqual(first["task_paths"], second["task_paths"])
        status = project_status(self.root, offline=True)
        self.assertEqual(status["learning_pr_count"], 1)
        self.assertEqual(status["pending_task_count"], 1)
        self.assertEqual(
            [path.name for path in (self.root / ".review" / "local" / "proposals" / "tasks").glob("*.json")],
            [Path(first["task_paths"][0]).name],
        )

    def test_same_knowledge_id_revisions_are_preserved_and_linked(self):
        self.save([
            {**self.pr, "number": 1, "created_at": "2026-01-01T00:00:00Z"},
            {**self.pr, "number": 2, "created_at": "2026-01-02T00:00:00Z",
             "reviews": [{"id": 22, "body": "Validate boundary input except after parser normalization.",
                          "state": "COMMENTED"}]},
        ])
        self.harvest(1)
        self.harvest(2)
        pending = {item["pr"]: item for item in project_status(self.root, offline=True, limit=10)["pending_tasks"]}
        self.propose_task(pending[1], candidate=self.candidate(
            revision=1,
            principle="Validate boundary input before use.",
        ))
        self.propose_task(pending[2], candidate=self.candidate(
            revision=2,
            principle="Validate external boundary input before use and document parser exceptions.",
            exceptions=["Internal parser helpers may rely on a prevalidated boundary."],
        ))
        status = project_status(self.root, offline=True)
        self.assertTrue(status["learning_complete"])
        self.assertEqual(status["knowledge_count"], 2)
        library = load_json(self.root / status["knowledge_path"])
        lineage = library["lineage"]
        self.assertEqual([item["knowledge_id"] for item in lineage], ["K-boundary"])
        self.assertEqual([item["revision"] for item in lineage[0]["revisions"]], [1, 2])
        self.assertEqual(lineage[0]["revisions"][0]["prs"], [1])
        self.assertEqual(lineage[0]["revisions"][1]["prs"], [2])
        self.assertNotEqual(lineage[0]["revisions"][0]["entry_ids"], lineage[0]["revisions"][1]["entry_ids"])

    def test_missing_offline_binding_is_a_visible_gap(self):
        harvested = self.harvest()
        task_path = self.root / harvested["task_paths"][0]
        task = load_json(task_path)
        del task["offline"]
        write_json(task_path, task)
        status = project_status(self.root, offline=True)
        self.assertFalse(status["learning_complete"])
        self.assertEqual(status["pending_task_count"], 0)
        self.assertEqual(status["learning_gap_count"], 1)
        self.assertIn("offline mode must be explicit", status["learning_gaps"][0]["reason"])
        self.assertEqual("partial", status["status"])

    def test_malformed_saved_proposal_identity_is_a_gap_not_a_crash(self):
        self.harvest()
        task = project_status(self.root, offline=True)["pending_tasks"][0]
        proposed = self.propose_task(task)
        manifest_path = Path(proposed["proposal"])
        manifest = load_json(manifest_path)
        manifest["task_id"] = ["not", "an", "identity"]
        write_json(manifest_path, manifest)
        status = project_status(self.root, offline=True)
        self.assertEqual("partial", status["status"])
        self.assertEqual(status["pending_task_count"], 1)
        self.assertFalse(status["learning_complete"])

    def test_unequal_same_revision_proposals_are_links_not_a_merged_principle(self):
        self.harvest()
        task = project_status(self.root, offline=True)["pending_tasks"][0]
        self.propose_task(task, candidate=self.candidate(principle="First scoped proposal."))
        self.propose_task(task, candidate=self.candidate(principle="A different scoped proposal."))
        status = project_status(self.root, offline=True)
        library = load_json(self.root / status["knowledge_path"])
        revision = library["lineage"][0]["revisions"][0]
        self.assertTrue(revision["has_alternatives"])
        self.assertEqual(2, len(revision["entry_ids"]))
        self.assertNotIn("principle", revision)
        self.assertEqual(2, len(library["entries"]))

    def test_live_and_fixture_learning_stay_isolated(self):
        offline = self.harvest()
        meta = collect._Fixture._metadata(self.pr)
        feedback = copy.deepcopy(self.pr)
        with patch.object(collect._GitHub, "pr", side_effect=lambda number: dict(meta)), \
                patch.object(collect._GitHub, "feedback", side_effect=lambda number, kind: copy.deepcopy(feedback[kind])):
            live = self.harvest(fixture=False)
        offline_status = project_status(self.root, offline=True)
        live_status = project_status(self.root)
        self.assertEqual(offline_status["pending_task_count"], 1)
        self.assertEqual(live_status["pending_task_count"], 1)
        self.assertNotEqual(offline_status["pending_tasks"][0]["task_id"], live_status["pending_tasks"][0]["task_id"])
        self.assertNotEqual(offline["task_paths"][0], live["task_paths"][0])

    def test_empty_learning_result_counts_as_completed_without_knowledge(self):
        harvested = self.harvest()
        status = project_status(self.root, offline=True)
        self.assertEqual(status["pending_tasks"][0]["path"], harvested["task_paths"][0])
        self.propose_task(status["pending_tasks"][0], empty=True)
        learned = project_status(self.root, offline=True)
        self.assertTrue(learned["learning_complete"])
        self.assertEqual(learned["completed_task_count"], 1)
        self.assertEqual(learned["knowledge_count"], 0)


if __name__ == "__main__":
    unittest.main()
