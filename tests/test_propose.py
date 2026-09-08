import copy
import tempfile
import unittest
from pathlib import Path

from tests.test_core import knowledge
from repowise.common import Error, digest, load_json, write_json
from repowise.core import initialize
from repowise.propose import propose


class ProposalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize(self.root, "example/project")
        bundle = {"evidence_id": "E-1", "repository": "example/project", "pr": 1,
                  "available_at": "2026-01-01T00:00:00Z"}
        input_data = {"repository": "example/project", "evidence": [bundle]}
        self.task = {"task_type": "induce", "task_id": "T-1", "input_hash": digest(input_data),
                     "repository": "example/project", "evidence": [bundle], "input": input_data}
        self.candidate = knowledge()
        self.candidate["maturity"] = "candidate"
        self.candidate["sources"] = []
        self.candidate["evidence_ids"] = ["E-1"]
        self.response = {
            "task_id": "T-1", "input_hash": digest(input_data),
            "model": {"provider": "host", "model": "unknown", "prompt_version": "induce-v1"},
            "candidates": [self.candidate],
        }
        self.task_path = self.root / "task.json"
        self.response_path = self.root / "response.json"
        write_json(self.task_path, self.task)

    def run_propose(self, response=None):
        write_json(self.response_path, self.response if response is None else response)
        return propose(self.root, self.task_path, self.response_path)

    def test_proposal_is_local_and_idempotent(self):
        result = self.run_propose()
        manifest = load_json(Path(result["proposal"]))
        self.assertEqual(manifest["candidates"][0]["knowledge"]["sources"][0]["reference"], "E-1")
        self.assertEqual(manifest["candidates"][0]["independence"], "unverified")
        self.assertEqual(self.run_propose()["status"], "already_proposed")
        self.assertFalse(list((self.root / ".review" / "knowledge").glob("*")))

    def test_idempotent_retry_revalidates_saved_proposal(self):
        result = self.run_propose()
        path = Path(result["proposal"])
        manifest = load_json(path)
        manifest["response_hash"] = "changed"
        write_json(path, manifest)
        with self.assertRaisesRegex(Error, "bindings changed"):
            self.run_propose()

    def test_unknown_evidence_rejected(self):
        self.candidate["evidence_ids"] = ["E-missing"]
        with self.assertRaises(Error):
            self.run_propose()

    def test_cannot_self_approve(self):
        self.candidate["maturity"] = "approved"
        with self.assertRaises(Error):
            self.run_propose()

    def test_mismatched_task_and_provider_rejected(self):
        for field, value in (("task_id", "other"), ("input_hash", "other")):
            response = copy.deepcopy(self.response)
            response[field] = value
            with self.assertRaises(Error):
                self.run_propose(response)
        self.response["model"]["provider"] = "unapproved"
        with self.assertRaises(Error):
            self.run_propose()

    def test_empty_candidates_preserved(self):
        self.response["candidates"] = []
        self.assertEqual(self.run_propose()["candidate_count"], 0)

    def test_modified_task_rejected(self):
        self.task["evidence"][0]["pr"] = 99
        write_json(self.task_path, self.task)
        with self.assertRaises(Error):
            self.run_propose()

    def test_missing_bound_evidence_time_is_not_replaced_with_current_time(self):
        self.task["evidence"][0].pop("available_at")
        self.task["input_hash"] = digest(self.task["input"])
        self.response["input_hash"] = self.task["input_hash"]
        write_json(self.task_path, self.task)
        with self.assertRaisesRegex(Error, "bound availability timestamp"):
            self.run_propose()


if __name__ == "__main__":
    unittest.main()
