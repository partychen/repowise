import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_core import knowledge
from review_memory.cli import main
from review_memory.common import utcnow
from review_memory.review_contract import REPOSITORY_DIMENSIONS


PROJECT = Path(__file__).resolve().parents[1]


class ExternalWorkflowTests(unittest.TestCase):
    def test_learning_signed_approval_review_and_replay_leave_target_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            target = temporary / "code"
            target.mkdir()
            (target / "src").mkdir()
            (target / ".gitignore").write_text("preserve-this-pattern\n")
            source = target / "src" / "lib.rs"
            source.write_text("pub fn boundary() {}\n")

            def git(root, *arguments):
                return subprocess.run(
                    ["git", "-C", str(root), "-c", "user.name=Test",
                     "-c", "user.email=test@example.test", "-c", "commit.gpgsign=false",
                     *arguments], check=True, capture_output=True, text=True, env=dict(os.environ),
                ).stdout.strip()

            git(target, "init", "-q")
            git(target, "add", ".")
            git(target, "commit", "-qm", "Synthetic code base")
            base = git(target, "rev-parse", "HEAD")
            source.write_text("pub fn boundary() {\n    let status = true;\n}\n")
            git(target, "add", ".")
            git(target, "commit", "-qm", "Synthetic code change")
            head = git(target, "rev-parse", "HEAD")

            def tree():
                return {p.relative_to(target).as_posix(): p.read_bytes()
                        for p in target.rglob("*") if p.is_file()}

            before = tree()
            data_home = temporary / "memory"

            def invoke(*arguments, expected_code=0):
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = main(["--root", str(target), "--data-home", str(data_home), *arguments])
                self.assertEqual(code, expected_code, output.getvalue() + error.getvalue())
                result = json.loads(output.getvalue())
                self.assertEqual(result["target_root"], str(target))
                self.assertTrue(Path(result["storage_root"]).is_relative_to(data_home))
                return result

            collected = invoke(
                "sync", "--repository", "example/project",
                "--fixture", str(PROJECT / "examples" / "collection.json"),
            )
            storage = Path(collected["storage_root"])
            local = Path(collected["local_path"])
            task_path = storage / collected["pending_tasks"][0]["path"]
            task = json.loads(task_path.read_text(encoding="utf-8"))
            candidate = knowledge()
            candidate.update(maturity="candidate", sources=[],
                             evidence_ids=[task["evidence"][0]["evidence_id"]])
            response_path = local / "synthetic-induction.json"
            response_path.write_text(json.dumps({
                "task_id": task["task_id"], "input_hash": task["input_hash"],
                "model": {"provider": "host", "model": "synthetic", "prompt_version": "external-workflow"},
                "candidates": [candidate],
            }), encoding="utf-8")
            proposed = invoke("propose", "--task", str(task_path), "--response", str(response_path))
            candidate_path = Path(proposed["proposal"]).parent / f"{candidate['id']}-r1.yaml"
            invoke("approval-queue")
            prepared = invoke(
                "prepare-approval", "--candidate", str(candidate_path), "--identity", "tester",
                "--owner", "test-maintainer", "--reason", "Synthetic fixture approval only",
            )
            self.assertEqual(list((storage / ".review" / "knowledge").glob("*.yaml")), [])

            # Simulate the human boundary with a fresh test-only key, never a real approval identity.
            key = temporary / "ephemeral-test-key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           capture_output=True, check=True)
            (storage / ".review" / "allowed_signers").write_text(
                "tester " + key.with_suffix(".pub").read_text(), encoding="utf-8")
            request = Path(prepared["request"])
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "review-memory-v1", str(request)],
                capture_output=True, check=True,
            )
            invoke("approve", "--request", str(request), "--signature", str(request) + ".sig")
            git(storage, "init", "-q")
            git(storage, "add", ".review")
            git(storage, "commit", "-qm", "Synthetic external policy")
            policy = git(storage, "rev-parse", "HEAD")
            self.assertEqual(git(storage, "ls-files", ".review/local", ".review/project.json"), "")
            self.assertEqual(invoke("snapshot", "--trusted-ref", policy)["knowledge_count"], 1)
            def pr_metadata(adapter, number):
                metadata = {
                    "number": number, "title": "Synthetic PR review", "body": "Synthetic change intent only.",
                    "base": {"sha": base, "repo": {"full_name": "example/project"}},
                    "head": {"sha": head, "repo": {"full_name": "example/project"}},
                }
                adapter.store.record(f"repos/example/project/pulls/{number}", metadata)
                return metadata

            with patch("review_memory.pull_requests._GitHub.pr", autospec=True, side_effect=pr_metadata):
                review = invoke("review", "--pr", "https://github.com/example/project/pull/1",
                                "--trusted-ref", policy, "--context-path", "src\\lib.rs")
            self.assertTrue(review["requires_response"])
            self.assertEqual(1, review["task"]["pull_request"]["number"])
            self.assertEqual("untrusted_change_description", review["task"]["pull_request"]["authority"])
            self.assertEqual(["src/lib.rs"], review["task"]["context_selection"]["requested_paths"])
            self.assertEqual(review["task"]["knowledge"][0]["id"], candidate["id"])

            def response_for(review_task, name):
                path = local / name
                path.write_text(json.dumps({
                    "schema_version": 2, "task_id": review_task["task_id"],
                    "input_hash": review_task["input_hash"],
                    "model": {"provider": "host", "model": "synthetic", "prompt_version": "external-workflow"},
                    "repository_assessments": [
                        {"dimension": dimension, "status": "needs_context", "references": [],
                         "rationale": "Synthetic protocol workflow only; no real host project assessment."}
                        for dimension in REPOSITORY_DIMENSIONS
                    ],
                    "assessments": [
                        {"knowledge_id": item["id"], "revision": item["revision"], "status": "checked",
                         "rationale": "Synthetic inspection only; no target commands executed."}
                        for item in review_task["knowledge"]
                    ],
                    "findings": [],
                }), encoding="utf-8")
                return path

            finalized = invoke("finalize", "--run-id", review["run_id"],
                               "--response", str(response_for(review["task"], "synthetic-review.json")),
                               expected_code=2)
            self.assertFalse(finalized["awaiting_response"])
            self.assertTrue(finalized["coverage_gaps"])
            self.assertEqual(set(REPOSITORY_DIMENSIONS),
                             {item["dimension"] for item in finalized["repository_assessments"]})
            now = datetime.now(timezone.utc)
            dataset = local / "synthetic-dataset.json"
            dataset.write_text(json.dumps({
                "schema_version": 1, "timeline": "simulated",
                "windows": {
                    "train": {"start": (now - timedelta(hours=2)).isoformat(),
                              "end": (now - timedelta(hours=1)).isoformat()},
                    "dev": {"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()},
                    "test": {"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat()},
                },
                "events": [{
                    "id": "synthetic-review", "type": "review",
                    "available_at": (now + timedelta(seconds=1)).isoformat(),
                    "repository": "example/project", "base": base, "head": head, "trusted_ref": policy,
                }],
            }), encoding="utf-8")
            replay = invoke("replay", "--dataset", str(dataset))
            for run in replay["runs"]:
                if run["requires_response"]:
                    replay_task = json.loads(
                        (Path(replay["runs_root"]) / run["run_id"] / "task.json").read_text(encoding="utf-8"))
                    invoke("finalize", "--replay-id", replay["replay_id"], "--run-id", run["run_id"],
                           "--response", str(response_for(replay_task, "synthetic-replay-response.json")),
                           expected_code=2)
            labels = local / "synthetic-labels.json"
            labels.write_text(json.dumps({
                "schema_version": 1, "replay_id": replay["replay_id"],
                "adjudicator": {"identity": "synthetic-human-label", "kind": "human", "independent": True},
                "cases": [{
                    "case_id": "synthetic-review", "reviewed_at": utcnow(),
                    "rationale": "Synthetic normal-control plumbing only, not product accuracy.",
                    "normal_control": True, "issues": [], "judgments": [],
                }],
            }), encoding="utf-8")
            invoke("score", "--replay-id", replay["replay_id"], "--labels", str(labels))
            self.assertEqual(before, tree())
            self.assertFalse((target / ".review").exists())

            feature = invoke("feature", "--goal", "Add a synthetic helper using the existing module.",
                             "--context-path", "src\\lib.rs", "--trusted-ref", policy)
            feature_task = feature["task"]
            self.assertFalse(feature["implemented"])
            self.assertEqual(candidate["id"], feature_task["knowledge"][0]["id"])
            self.assertEqual(before, tree())

            # Only this synthetic host action edits code; neither feature command does so.
            changed_source = source.read_bytes() + b"\npub fn feature_helper() -> bool { true }\n"
            source.write_bytes(changed_source)
            git(target, "--no-optional-locks", "diff", "--no-ext-diff", "--no-textconv", "--check")
            after_host = tree()
            expected = dict(before)
            expected["src/lib.rs"] = changed_source
            self.assertEqual(expected, after_host)
            feature_response = local / "synthetic-feature-response.json"
            feature_response.write_text(json.dumps({
                "schema_version": 1, "task_id": feature_task["task_id"],
                "input_hash": feature_task["input_hash"],
                "model": {"provider": "host", "model": "synthetic", "prompt_version": "external-workflow"},
                "status": "completed", "summary": "Synthetic authorized-host helper added.",
                "changes": [{"path": "src/lib.rs", "summary": "Added the synthetic helper."}],
                "checks": [{"command": "git diff --no-ext-diff --no-textconv --check",
                            "outcome": "passed", "summary": "The host ran the actual whitespace check; no Rust build."}],
                "knowledge_assessments": [
                    {"knowledge_id": item["id"], "revision": item["revision"], "status": "applied",
                     "rationale": "Synthetic knowledge-use record, not an effectiveness claim."}
                    for item in feature_task["knowledge"]
                ],
                "repository_assessments": [
                    {"dimension": dimension, "status": "needs_context", "references": [],
                     "rationale": "Synthetic scenario; no real architecture assessment or Rust execution."}
                    for dimension in REPOSITORY_DIMENSIONS
                ],
                "remaining_work": [],
            }), encoding="utf-8")
            completed = invoke("feature-finish", "--feature-id", feature["feature_id"],
                               "--response", str(feature_response), expected_code=2)
            self.assertEqual("completed", completed["host_status"])
            self.assertEqual("host_reported_not_independently_verified", completed["verification"])
            self.assertTrue(Path(completed["report_path"]).is_file())
            self.assertEqual(after_host, tree())


if __name__ == "__main__":
    unittest.main()
