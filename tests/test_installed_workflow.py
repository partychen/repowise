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
                   "REVIEW_MEMORY_CACHE": str(root / "runtime cache"),
                   "REVIEW_MEMORY_HOME": str(root / "project memory")}
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
            provenance = json.loads((skill / "wheels" / "provenance.json").read_text(encoding="utf-8"))
            self.assertTrue((skill / "wheels" / provenance["wheel"]["filename"]).is_file())
            self.assertTrue((skill / "wheels" / "LICENSE.PyYAML.txt").is_file())
            moved = root / "relocated skill with spaces"
            shutil.copytree(skill, moved)
            shutil.rmtree(installation)
            runner = moved / "scripts" / "main.py"
            (target / "review_memory.py").write_text('raise RuntimeError("Untrusted target import")')
            (target / ".gitignore").write_text("existing-ignore-pattern\n")
            target_before = {p.relative_to(target): p.read_bytes()
                             for p in target.rglob("*") if p.is_file()}
            env["PYTHONPATH"] = str(target)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                         "http_proxy", "https_proxy", "all_proxy"):
                env[name] = "http://127.0.0.1:1"
            env.update(NO_PROXY="", no_proxy="", PIP_INDEX_URL="https://untrusted.invalid/simple")

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
            storage = Path(first["storage_root"])
            self.assertTrue(storage.is_relative_to(root / "project memory"))
            self.assertFalse((target / ".review").exists())

            def learn(reference):
                task_path = storage / reference["path"]
                task = json.loads(task_path.read_text(encoding="utf-8"))
                candidate = knowledge()
                candidate.update(maturity="candidate", sources=[],
                                 evidence_ids=[task["evidence"][0]["evidence_id"]])
                response = {
                    "task_id": task["task_id"], "input_hash": task["input_hash"],
                    "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "installer-smoke"},
                    "candidates": [candidate],
                }
                response_path = storage / ".review" / "local" / "synthetic-response.json"
                response_path.write_text(json.dumps(response), encoding="utf-8")
                result = json.loads(invoke("propose", "--task", str(task_path), "--response", str(response_path)))
                return Path(result["proposal"]).parent / f"{candidate['id']}-r{candidate['revision']}.yaml"

            candidate_path = learn(first["pending_tasks"][0])
            second = json.loads(invoke("sync", "--fixture", str(fixture_path), "--max-prs", "1"))
            self.assertTrue(second["collection_complete"])
            self.assertEqual(second["pending_task_count"], 1)
            learn(second["pending_tasks"][0])
            status = json.loads(invoke("status", "--offline"))
            self.assertTrue(status["learning_complete"])
            self.assertEqual(status["knowledge_count"], 1)
            self.assertEqual(status["completed_task_count"], 2)
            self.assertTrue((storage / status["knowledge_path"]).is_file())
            self.assertFalse(status["next_actions"][0]["blocks_learning"])
            self.assertEqual(json.loads(invoke("sync", "--fixture", str(fixture_path)))["prs"], [])
            queue = json.loads(invoke("approval-queue", "--limit", "1"))
            self.assertEqual(queue["storage_root"], str(storage))
            prepared = json.loads(invoke(
                "prepare-approval", "--candidate", str(candidate_path),
                "--identity", "tester@example.test", "--owner", "test-maintainer",
                "--reason", "Synthetic unsigned installer handoff only",
            ))
            self.assertTrue(Path(prepared["request"]).is_file())
            self.assertFalse(list((storage / ".review" / "knowledge").glob("*.yaml")))
            replacement = root / "replacement skill"
            shutil.copytree(moved, replacement, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.rmtree(moved)
            runner = replacement / "scripts" / "main.py"
            resumed = json.loads(invoke("status", "--offline"))
            self.assertEqual(resumed["storage_root"], str(storage))
            self.assertEqual(resumed["knowledge_count"], 1)
            self.assertEqual(resumed["completed_task_count"], 2)
            self.assertEqual(target_before, {p.relative_to(target): p.read_bytes()
                                            for p in target.rglob("*") if p.is_file()})
            self.assertFalse((target / ".review").exists())


if __name__ == "__main__":
    unittest.main()
