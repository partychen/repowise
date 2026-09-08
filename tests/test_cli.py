import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_core import knowledge
from review_memory.cli import main, parser
from review_memory.common import Error
from review_memory.packs import list_packs, select_packs


class CliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_home = Path(temporary.name) / "memory"

    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-home", str(self.data_home), *args])
        return code, out.getvalue(), err.getvalue()

    def test_init_and_doctor(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["--root", directory]
            code, out, _ = self.call([*args, "init", "--repository", "Acme/Project"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["repository"], "acme/project")
            storage = Path(json.loads(out)["storage_root"])
            self.assertTrue((storage / ".review" / "config.yaml").is_file())
            self.assertFalse((Path(directory) / ".review").exists())
            code, out, _ = self.call([*args, "doctor"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["initialized"])

    def test_doctor_does_not_create_project_state(self):
        with tempfile.TemporaryDirectory() as directory:
            code, out, error = self.call(["--root", directory, "doctor"])
            self.assertEqual(code, 0, error)
            result = json.loads(out)
            self.assertFalse(result["initialized"])
            self.assertEqual(result["root"], str(Path(directory).resolve()))
            self.assertFalse(self.data_home.exists())
            self.assertFalse((Path(directory) / ".review").exists())

    def test_old_target_review_directory_is_ignored_and_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            old = target / ".review"
            old.mkdir()
            (old / "config.yaml").write_text("not: a-valid-project-configuration\n")
            (target / ".gitignore").write_text("existing-pattern\n")
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            code, out, error = self.call(["--root", directory, "init", "--repository", "acme/project"])
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(out)["repository"], "acme/project")
            self.assertEqual(before, {p.relative_to(target): p.read_bytes()
                                      for p in target.rglob("*") if p.is_file()})

    def test_fixture_progress_never_suggests_an_unqualified_live_sync(self):
        result = {"remaining_pr_count": 1, "pending_task_count": 0, "knowledge_count": 0}
        with tempfile.TemporaryDirectory() as directory, \
                patch("review_memory.sync.sync_project", return_value=result):
            code, out, error = self.call([
                "--root", directory, "sync", "--repository", "example/project",
                "--fixture", str(Path(directory) / "synthetic.json"),
            ])
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(out)["next_actions"], [])

    def test_partial_result_not_success_exit(self):
        with patch("review_memory.cli.dispatch", return_value={"status": "partial", "complete": False}):
            self.assertEqual(self.call(["doctor"])[0], 2)

    def test_learning_gaps_cannot_be_hidden_by_completed_collection(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "review_memory.sync.sync_project",
                return_value={"status": "complete", "complete": True, "learning_gap_count": 1,
                              "knowledge_count": 0, "pending_task_count": 0}):
            code, out, error = self.call([
                "--root", directory, "sync", "--repository", "example/project",
                "--fixture", str(Path(directory) / "synthetic.json"),
            ])
            self.assertEqual(2, code, error)
            self.assertEqual("partial", json.loads(out)["status"])

    def test_review_context_paths_are_repeatable_and_portable(self):
        args = parser().parse_args([
            "review", "--repository", "acme/project", "--base", "base", "--head", "head",
            "--trusted-ref", "policy", "--context-path", "src\\shared.rs",
            "--context-path", "Cargo.toml",
        ])
        self.assertEqual(["src/shared.rs", "Cargo.toml"], args.context_path)
        self.assertEqual(100, args.max_files)
        self.assertEqual(500000, args.max_bytes)

    def test_expected_error_is_json(self):
        with patch("review_memory.cli.dispatch", side_effect=Error("Invalid input")):
            code, out, err = self.call(["doctor"])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertEqual(json.loads(err)["status"], "error")

    def test_pack_reference_authority_and_budget(self):
        packs = list_packs()
        self.assertGreaterEqual(len(packs), 5)
        self.assertTrue(all(pack["authority"] == "reference" for pack in packs))
        result = select_packs("Rust ownership async unsafe errors performance", limit=1)
        self.assertEqual(len(result["packs"]), 1)
        self.assertEqual(result["omitted_count"], result["matched_count"] - 1)
        with self.assertRaises(Error):
            select_packs("Rust", 0)


if __name__ == "__main__":
    unittest.main()
