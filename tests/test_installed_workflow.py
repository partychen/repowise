"""Opt-in real installer smoke: no user Skill registration or project data is changed."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_core import knowledge


PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("REVIEW_MEMORY_TEST_INSTALL") == "1",
                     "Set REVIEW_MEMORY_TEST_INSTALL=1 to exercise npx installation and dependency setup.")
class InstalledWorkflowTests(unittest.TestCase):
    def test_npx_install_move_sync_induce_and_resume(self):
        npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
        self.assertIsNotNone(npx, "Node.js/npx is required for the installer smoke.")
        with tempfile.TemporaryDirectory(prefix="review memory install ") as temporary:
            root = Path(temporary).resolve()
            installation = root / "installation project"
            target = root / "learning project"
            installation.mkdir()
            target.mkdir()
            env = {**os.environ, "CI": "1", "DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
                   "REVIEW_MEMORY_CACHE": str(root / "runtime cache")}
            result = subprocess.run(
                [npx, "--yes", "skills", "add", str(PROJECT), "--skill", "review-memory",
                 "--agent", "github-copilot", "--copy", "--yes"],
                cwd=installation, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            matches = list(installation.rglob("SKILL.md"))
            self.assertTrue(matches, result.stdout)
            skill = next(path.parent for path in matches if path.parent.name == "review-memory")
            self.assertTrue((skill / "requirements.txt").is_file())
            self.assertTrue((skill / "scripts" / "review_memory" / "sync.py").is_file())
            moved = root / "relocated skill with spaces"
            shutil.copytree(skill, moved)
            shutil.rmtree(installation)
            runner = moved / "scripts" / "main.py"
            (target / "review_memory.py").write_text('raise RuntimeError("Untrusted target import")')
            env["PYTHONPATH"] = str(target)

            def invoke(*args, code=0):
                completed = subprocess.run(
                    [sys.executable, "-I", str(runner), *args], cwd=target, env=env,
                    capture_output=True, text=True, encoding="utf-8",
                    timeout=600 if args == ("setup",) else 180,
                )
                self.assertEqual(completed.returncode, code, completed.stdout + completed.stderr)
                return completed.stdout

            invoke("setup")
            self.assertEqual(len(json.loads(invoke("packs", "list"))["packs"]), 17)
            fixture = json.loads((PROJECT / "examples" / "collection.json").read_text(encoding="utf-8"))
            second = copy.deepcopy(fixture["prs"][0])
            second["number"] += 1
            fixture["prs"] = [fixture["prs"][0], second]
            fixture_path = root / "synthetic.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            first = json.loads(invoke("sync", "--repository", fixture["repository"],
                                      "--fixture", str(fixture_path), "--max-prs", "1", code=2))
            self.assertEqual(first["remaining_pr_count"], 1)

            def learn(reference):
                task_path = target / reference["path"]
                task = json.loads(task_path.read_text(encoding="utf-8"))
                candidate = knowledge()
                candidate.update(maturity="candidate", sources=[],
                                 evidence_ids=[task["evidence"][0]["evidence_id"]])
                response = {
                    "task_id": task["task_id"], "input_hash": task["input_hash"],
                    "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "installer-smoke"},
                    "candidates": [candidate],
                }
                response_path = target / ".review" / "local" / "synthetic-response.json"
                response_path.write_text(json.dumps(response), encoding="utf-8")
                invoke("propose", "--task", str(task_path), "--response", str(response_path))

            learn(first["pending_tasks"][0])
            second = json.loads(invoke("sync", "--fixture", str(fixture_path), "--max-prs", "1"))
            self.assertTrue(second["collection_complete"])
            self.assertEqual(second["pending_task_count"], 1)
            learn(second["pending_tasks"][0])
            status = json.loads(invoke("status", "--offline"))
            self.assertTrue(status["learning_complete"])
            self.assertEqual(status["knowledge_count"], 1)
            self.assertEqual(status["completed_task_count"], 2)
            self.assertTrue((target / status["knowledge_path"]).is_file())
            self.assertEqual(json.loads(invoke("sync", "--fixture", str(fixture_path)))["prs"], [])
            self.assertFalse(list((target / ".review" / "knowledge").glob("*.yaml")))


if __name__ == "__main__":
    unittest.main()
