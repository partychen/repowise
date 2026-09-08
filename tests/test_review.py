import copy
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "skills" /
                       "repowise" / "scripts"))

from repowise.common import Error, digest, load_json, write_json
from repowise.core import initialize, load_git_snapshot
from repowise.detectors import TOOL_ID
from repowise.render import publish, render_html
from repowise.review import _added_lines, _source_lines, finalize_review, prepare_review
from tests.test_core import file_tree, fixture_directory, git_command, initialize_git


def rule(mode="llm", identifier="K-review"):
    return {"schema_version": 1, "id": identifier, "revision": 1, "execution": mode,
            "enforcement": "advisory", "title": "Inspect boundary", "principle": "Preserve boundaries",
            "applicability": {"paths": ["**"], "exclusions": [], "context": ""},
            "detector_ref": "D-deps-001", "examples": {"valid": [], "violating": []}}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(fixture_directory())
        self.root, self.memory = self.directory / "code", self.directory / "policy"
        initialize_git(self.root)
        initialize_git(self.memory)
        initialize(self.memory, "owner/repo")
        git_command(self.memory, "add", ".review")
        git_command(self.memory, "commit", "-qm", "Independent synthetic policy store")
        self.policy = git_command(self.memory, "rev-parse", "HEAD")
        self.write("source file.py", "old = 1\nunchanged = 2\n")
        self.write("Cargo.toml", '[package]\nname = "boundary"\nversion = "0.1.0"\n')
        self.commit()
        self.base = self.command("rev-parse", "HEAD")
        self.write("source file.py", "new = 1\nunchanged = 2\n")
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        self.snapshot = {"hash": "snapshot-hash", "trusted_sha": self.policy, "knowledge": [rule()],
                         "detectors": [], "manifest": {"coverage_gaps": []},
                         "config": {"repository": "owner/repo", "max_rules_per_run": 50,
                                    "max_findings_per_pr": 10}}
        self.loader = patch("repowise.core.load_git_snapshot", side_effect=lambda *a, **k: self.snapshot)
        self.load = self.loader.start()
        self.addCleanup(self.loader.stop)

    def command(self, *args):
        return git_command(self.root, *args)

    def write(self, path, text):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8"))

    def commit(self):
        self.command("add", "--all")
        self.command("commit", "--quiet", "-m", "Fixture change")

    def prepare(self, **kwargs):
        return prepare_review(self.root, "owner/repo", self.base, self.head, self.policy,
                              memory_root=self.memory, **kwargs)

    def response(self, prepared, findings=True):
        task = prepared["task"]
        return {"schema_version": 2, "task_id": task["task_id"], "input_hash": task["input_hash"],
                "model": {"provider": "unknown", "model": "offline-fixture", "prompt_version": "v1"},
                "repository_assessments": [],
                "assessments": [{"knowledge_id": "K-review", "revision": 1, "status": "checked",
                                 "rationale": "Inspected supplied source and counterexamples."}],
                "findings": [{
                    "knowledge_id": "K-review", "revision": 1, "basis": "behavior", "comparisons": [],
                    "evidence": {"path": "source file.py", "line_start": 1, "line_end": 1, "text": "new = 1"},
                    "applicability_rationale": "The changed source is in the approved scope.",
                    "counterexample_checks": ["Compared the previous assignment."], "impact": "Example impact",
                    "triggering_conditions": "When imported", "suggestion": "Inspect boundary semantics",
                    "uncertainty": "No execution performed", "verification": "inspected"}] if findings else []}

    def finalize(self, prepared, response):
        path = self.memory / ".review" / "local" / "response.json"
        write_json(path, response)
        return finalize_review(self.memory, prepared["run_id"], path)

    def test_pinned_source_and_input_identity(self):
        self.write("source file.py", "uncommitted malicious = 99\n")
        before_head = self.command("rev-parse", "HEAD")
        before = file_tree(self.root)
        prepared = self.prepare(at="2025-01-01T00:00:00Z")
        self.load.assert_called_once_with(self.memory, self.policy, at="2025-01-01T00:00:00Z")
        task = prepared["task"]
        self.assertEqual(self.head, task["head_sha"])
        self.assertEqual(self.policy, task["trusted_sha"])
        self.assertEqual("target_repository", task["code_source"])
        self.assertEqual("external_memory", task["policy_source"])
        self.assertEqual(".review/local/runs", task["runs_path"])
        self.assertEqual(before_head, self.command("rev-parse", "HEAD"))
        self.assertEqual(before, file_tree(self.root))
        context = next(c for c in task["code_context"] if c["path"] == "source file.py")
        self.assertEqual("new = 1\nunchanged = 2\n", context["after"])
        self.assertEqual([1], context["changed_lines"])
        self.assertEqual(task["input_hash"], digest({k: v for k, v in task.items() if k != "input_hash"}))
        self.assertIn("uncommitted", (self.root / "source file.py").read_text())

    def test_every_git_read_in_preparation_disables_mutable_object_behavior(self):
        from repowise import common
        with patch("repowise.common.subprocess.run", wraps=common.subprocess.run) as calls:
            self.prepare()
        commands = [call.args[0] for call in calls.call_args_list if call.args[0][0] == "git"]
        self.assertTrue(any("rev-parse" in command for command in commands))
        self.assertTrue(any("diff" in command for command in commands))
        self.assertTrue(any("cat-file" in command for command in commands))
        for command in commands:
            for option in ("--no-replace-objects", "--no-lazy-fetch", "--no-optional-locks"):
                with self.subTest(command=command, option=option):
                    self.assertEqual(1, command.count(option))

    def test_finalize_exact_evidence_and_immutable_completion(self):
        before = file_tree(self.root)
        prepared = self.prepare()
        response = self.response(prepared)
        report = self.finalize(prepared, response)
        self.assertEqual(1, report["total_findings"])
        self.assertEqual(1, report["findings"][0]["inline_line"])
        self.assertFalse(report["cache_reuse"])
        self.assertEqual("external_memory", report["policy_source"])
        self.assertEqual(self.policy, report["trusted_sha"])
        self.assertIsNone(report["costs"]["amount"])
        self.assertEqual(report, self.finalize(prepared, response))
        response["findings"][0]["suggestion"] = "Different response"
        with self.assertRaisesRegex(Error, "already completed"):
            self.finalize(prepared, response)
        directory = Path(prepared["task_path"]).parent
        self.assertTrue((directory / "report.html").exists())
        self.assertTrue(directory.is_relative_to(self.memory / ".review" / "local"))
        self.assertEqual(report, load_json(directory / "report.json"))
        self.assertEqual(before, file_tree(self.root))

    def test_bad_evidence_stale_unknown_and_execution_claims_rejected(self):
        prepared = self.prepare()
        response = self.response(prepared)
        variants = []
        for key, value in (("text", "invented"), ("line_start", 0), ("line_end", 999),
                           ("path", "../secret"), ("path", "missing.py")):
            candidate = copy.deepcopy(response)
            candidate["findings"][0]["evidence"][key] = value
            variants.append(candidate)
        candidate = copy.deepcopy(response)
        candidate["input_hash"] = "stale"
        variants.append(candidate)
        candidate = copy.deepcopy(response)
        candidate["findings"][0]["revision"] = 2
        variants.append(candidate)
        candidate = copy.deepcopy(response)
        candidate["findings"][0]["verification"] = "executed"
        variants.append(candidate)
        candidate = copy.deepcopy(response)
        candidate["suppressed_static_findings"] = []
        variants.append(candidate)
        candidate = copy.deepcopy(response)
        candidate["model"]["model"] = ""
        variants.append(candidate)
        for candidate in variants:
            with self.subTest(candidate=candidate):
                with self.assertRaises(Error):
                    self.finalize(prepared, candidate)
        self.assertEqual(1, self.finalize(prepared, response)["total_findings"])

    def test_missing_assessment_is_coverage_gap(self):
        prepared = self.prepare()
        response = self.response(prepared, findings=False)
        response["assessments"] = []
        report = self.finalize(prepared, response)
        self.assertEqual("incomplete", report["status"])
        self.assertTrue(any("Missing semantic assessment" in gap for gap in report["coverage_gaps"]))

    def test_repository_first_contract_and_missing_dimensions_are_visible(self):
        from repowise.review_contract import REPOSITORY_DIMENSIONS
        prepared = self.prepare()
        task = prepared["task"]
        self.assertEqual(2, task["schema_version"])
        self.assertEqual(2, task["output_contract"]["schema_version"])
        self.assertEqual(REPOSITORY_DIMENSIONS, task["repository_review"]["dimensions"])
        self.assertIn("HEAD-only examples cannot establish pre-existing convention",
                      task["output_contract"]["instructions"])
        self.assertIn("context_selection", task)
        report = self.finalize(prepared, self.response(prepared, findings=False))
        self.assertEqual([], report["repository_assessments"])
        for dimension in REPOSITORY_DIMENSIONS:
            self.assertIn(f"Missing repository assessment: {dimension}", report["coverage_gaps"])
        self.assertFalse(report["awaiting_response"])
        self.assertEqual("incomplete", report["status"])

    def test_returned_task_cannot_mutate_the_next_review_contract(self):
        prepared = self.prepare()
        prepared["task"]["output_contract"]["instructions"] = "Untrusted replacement."
        prepared["task"]["repository_review"]["dimensions"]["architecture"] = "Skip it."
        next_task = self.prepare()["task"]
        self.assertNotEqual("Untrusted replacement.", next_task["output_contract"]["instructions"])
        self.assertNotEqual("Skip it.", next_task["repository_review"]["dimensions"]["architecture"])

    def test_consistency_evidence_is_verified_and_rendered_from_base(self):
        prepared = self.prepare()
        response = self.response(prepared)
        reference = {"snapshot": "base", "commit": self.base, "path": "source file.py",
                     "line_start": 1, "line_end": 1, "text": "old = 1"}
        response["findings"][0].update(basis="consistency", comparisons=[reference])
        response["repository_assessments"] = [
            {"dimension": "idioms", "status": "checked", "references": [reference],
             "rationale": "Synthetic comparison fixture, not a real judgment of style."}
        ]
        report = self.finalize(prepared, response)
        self.assertEqual([reference], report["findings"][0]["comparisons"])
        self.assertEqual(response["repository_assessments"], report["repository_assessments"])
        rendered = (Path(prepared["task_path"]).parent / "report.md").read_text(encoding="utf-8")
        self.assertIn("Existing project comparisons", rendered)
        self.assertIn("BASE " + self.base, rendered)
        self.assertIn("idioms: checked", rendered)

    def test_head_only_example_cannot_justify_existing_convention(self):
        self.write("new_example.py", "new_pattern = 7\n")
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        prepared = self.prepare()
        response = self.response(prepared)
        finding = response["findings"][0]
        finding["basis"] = "consistency"
        reference = {"snapshot": "head", "commit": self.head, "path": "new_example.py",
                     "line_start": 1, "line_end": 1, "text": "new_pattern = 7"}
        finding["comparisons"] = [reference]
        with self.assertRaisesRegex(Error, "BASE evidence"):
            self.finalize(prepared, response)
        reference.update(snapshot="base", commit=self.base)
        with self.assertRaisesRegex(Error, "available frozen source"):
            self.finalize(prepared, response)
        finding.update(basis="behavior", comparisons=[])
        self.assertEqual(1, self.finalize(prepared, response)["total_findings"])

    def test_model_provider_must_match_trusted_configuration(self):
        self.snapshot["config"]["model_provider"] = "host"
        prepared = self.prepare()
        response = self.response(prepared)
        with self.assertRaisesRegex(Error, "provider"):
            self.finalize(prepared, response)
        response["model"]["provider"] = "host"
        self.assertEqual("host", self.finalize(prepared, response)["model"]["provider"])

    def test_external_references_are_frozen_but_not_policy(self):
        prepared = self.prepare(reference_query="Rust ownership", reference_limit=1)
        task = prepared["task"]
        self.assertEqual(1, len(task["reference_packs"]))
        pack = task["reference_packs"][0]
        self.assertEqual("reference", pack["authority"])
        self.assertNotIn(pack["id"], [knowledge["id"] for knowledge in task["knowledge"]])
        response = self.response(prepared)
        response["findings"][0]["knowledge_id"] = pack["id"]
        with self.assertRaisesRegex(Error, "Unknown or stale"):
            self.finalize(prepared, response)
        with patch("repowise.packs.select_packs", side_effect=AssertionError("Do not reload mutable references")):
            report = self.finalize(prepared, self.response(prepared, findings=False))
        self.assertEqual(pack["content_hash"], report["external_references"][0]["content_hash"])

    def test_current_external_references_cannot_leak_into_replay(self):
        with self.assertRaisesRegex(Error, "temporal replay"):
            self.prepare(at="2025-01-01T00:00:00Z", reference_query="Rust ownership")

    def test_reference_budget_reports_omitted_packs(self):
        from repowise.packs import list_packs
        oversized = copy.deepcopy(list_packs()[0])
        oversized["summary"] = "x" * 64001
        oversized["content_hash"] = digest(oversized)
        selection = {"authority": "reference", "query": "Rust", "packs": [oversized],
                     "matched_count": 1, "omitted_count": 0}
        with patch("repowise.packs.select_packs", return_value=selection):
            prepared = self.prepare(reference_query="Rust")
        self.assertEqual([], prepared["task"]["reference_packs"])
        self.assertEqual(1, prepared["task"]["reference_selection"]["budget_omitted_count"])

    def test_static_only_accepts_empty_finalization_without_model_run(self):
        self.snapshot["knowledge"] = []
        self.snapshot["config"]["model_provider"] = "host"
        prepared = self.prepare()
        response = self.response(prepared, findings=False)
        response["assessments"] = []
        response["model"] = {"provider": "unknown", "model": "unknown", "prompt_version": "unknown"}
        report = self.finalize(prepared, response)
        self.assertEqual(prepared["report"], report)
        self.assertEqual("unknown", report["model"]["model"])
        self.assertEqual(report, self.finalize(prepared, response))
        response["model"]["model"] = "different"
        with self.assertRaisesRegex(Error, "already completed"):
            self.finalize(prepared, response)

    def test_task_mutation_is_rejected(self):
        prepared = self.prepare()
        task = load_json(Path(prepared["task_path"]))
        task["head_sha"] = self.base
        write_json(Path(prepared["task_path"]), task)
        with self.assertRaisesRegex(Error, "modified"):
            self.finalize(prepared, self.response(prepared))

    def test_run_traversal_is_rejected(self):
        path = self.memory / ".review" / "local" / "response.json"
        write_json(path, {})
        with self.assertRaisesRegex(Error, "run id"):
            finalize_review(self.memory, "../../outside", path)

    def test_evaluation_runs_are_isolated_and_paths_confined(self):
        before = file_tree(self.root)
        runs_root = self.memory / ".review" / "local" / "evaluation" / "fixture" / "runs"
        prepared = self.prepare(runs_root=runs_root)
        self.assertEqual(runs_root / prepared["run_id"] / "task.json", Path(prepared["task_path"]))
        self.assertEqual([], list((self.memory / ".review" / "local" / "runs").iterdir()))
        path = self.memory / ".review" / "local" / "response.json"
        write_json(path, self.response(prepared))
        result = finalize_review(self.memory, prepared["run_id"], path, runs_root=runs_root)
        self.assertEqual(1, result["total_findings"])
        with self.assertRaises(Error):
            self.prepare(runs_root=self.root.parent / "outside")
        with self.assertRaises(Error):
            self.prepare(runs_root=Path(".review") / "local" / "evaluation" / ".." / ".." / "runs")
        with self.assertRaisesRegex(Error, "external memory"):
            self.prepare(runs_root=self.root / ".review" / "local" / "runs")
        self.assertEqual(before, file_tree(self.root))

    def test_copied_evaluation_task_cannot_be_finalized_as_production(self):
        runs_root = self.memory / ".review" / "local" / "evaluation" / "fixture" / "runs"
        prepared = self.prepare(runs_root=runs_root)
        shutil.copytree(runs_root / prepared["run_id"],
                        self.memory / ".review" / "local" / "runs" / prepared["run_id"])
        with self.assertRaisesRegex(Error, "isolated runs directory"):
            self.finalize(prepared, self.response(prepared))

    def test_limits_and_manual_rules_are_disclosed(self):
        self.snapshot["knowledge"] = [rule("manual"), rule(identifier="K-overflow")]
        self.snapshot["config"]["max_rules_per_run"] = 1
        prepared = self.prepare(max_bytes=1, max_files=1)
        gaps = prepared["report"]["coverage_gaps"]
        self.assertTrue(any("Byte limit" in gap for gap in gaps))
        self.assertTrue(any("File limit" in gap for gap in gaps))
        self.assertTrue(any("Rule limit" in gap for gap in gaps))
        self.assertTrue(any("manual coverage" in gap for gap in gaps))
        self.assertEqual("incomplete", prepared["report"]["status"])

    def test_display_cap_retains_full_deduplicated_results(self):
        self.snapshot["config"]["max_findings_per_pr"] = 1
        prepared = self.prepare()
        response = self.response(prepared)
        second = copy.deepcopy(response["findings"][0])
        second["evidence"] = {"path": "source file.py", "line_start": 2, "line_end": 2, "text": "unchanged = 2"}
        response["findings"].extend([second, copy.deepcopy(second)])
        report = self.finalize(prepared, response)
        self.assertEqual(2, report["total_findings"])
        self.assertEqual(1, report["omitted_findings"])
        self.assertEqual(1, len(report["displayed_findings"]))
        existing = next(f for f in report["findings"] if f["evidence"]["line_start"] == 2)
        self.assertEqual("unknown", existing["novelty"])
        self.assertTrue(existing["evidence_pre_existing"])
        self.assertEqual("summary", existing["placement"])

    def test_rename_deleted_binary_and_generated_files(self):
        self.base = self.head
        self.command("mv", "source file.py", "renamed file.py")
        self.write("renamed file.py", "new = 1\nunchanged = 2\nthird = 3\n")
        (self.root / "Cargo.toml").unlink()
        (self.root / "blob.dat").write_bytes(b"a\x00b")
        self.write("generated.txt", "generated\n")
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        self.snapshot["config"]["generated_paths"] = ["generated.*"]
        prepared = self.prepare()
        contexts = {c["path"]: c for c in prepared["task"]["code_context"]}
        self.assertIn("renamed file.py", contexts)
        self.assertEqual("source file.py", contexts["renamed file.py"]["old_path"])
        self.assertTrue(contexts["renamed file.py"]["status"].startswith("R"))
        self.assertEqual([3], contexts["renamed file.py"]["changed_lines"])
        self.assertIsNone(contexts["Cargo.toml"]["after"])
        self.assertTrue(any("Binary" in g for g in prepared["report"]["coverage_gaps"]))
        self.assertTrue(any("Excluded/generated" in g for g in prepared["report"]["coverage_gaps"]))

    def test_static_detector_is_separately_approved_and_cannot_be_suppressed(self):
        self.write("Cargo.toml", '[package]\nname = "boundary"\nversion = "0.1.0"\n'
                   '[dependencies]\nforbidden = "1"\n')
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        self.snapshot["knowledge"] = [rule("hybrid")]
        unapproved = self.prepare()
        self.assertEqual(0, unapproved["report"]["total_findings"])
        self.assertTrue(any("not separately approved" in g for g in unapproved["report"]["coverage_gaps"]))
        self.snapshot["detectors"] = [{"id": "D-deps-001", "revision": 1, "tool_id": TOOL_ID,
                                     "parameters": {"from_package": "boundary", "to_package": "forbidden"}}]
        prepared = self.prepare()
        response = self.response(prepared, findings=False)
        report = self.finalize(prepared, response)
        self.assertEqual(1, report["total_findings"])
        self.assertEqual("static", report["findings"][0]["origin"])
        self.assertEqual("introduced", report["findings"][0]["novelty"])
        self.assertEqual("executed", report["findings"][0]["verification"])
        self.assertEqual(0, report["costs"]["static"]["llm_tokens"])
        self.assertEqual(1, report["costs"]["static"]["executions"])
        self.assertGreaterEqual(report["costs"]["static"]["elapsed_seconds"], 0)

    def test_structured_snapshot_gaps_remain_visible(self):
        self.snapshot["knowledge"] = []
        self.snapshot["manifest"]["coverage_gaps"] = [
            {"knowledge_id": "K-expired", "reason": "Expired policy requires review."}
        ]
        prepared = self.prepare()
        self.assertEqual("incomplete", prepared["report"]["status"])
        self.assertIn("K-expired: Expired policy requires review.", prepared["report"]["coverage_gaps"])

    def test_scoped_static_rule_runs_for_workspace_dependency_change(self):
        self.write("Cargo.toml", '[workspace.dependencies]\nalias = {package = "allowed", version = "1"}\n')
        self.write("member/Cargo.toml", '[package]\nname = "boundary"\nversion = "0.1.0"\n'
                   '[dependencies]\nalias.workspace = true\n')
        self.commit()
        self.base = self.command("rev-parse", "HEAD")
        self.write("Cargo.toml", '[workspace.dependencies]\nalias = {package = "forbidden", version = "1"}\n')
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        scoped = rule("static")
        scoped["applicability"]["paths"] = ["member/**"]
        self.snapshot["knowledge"] = [scoped]
        self.snapshot["detectors"] = [{"id": "D-deps-001", "revision": 1, "tool_id": TOOL_ID,
                                     "parameters": {"from_package": "boundary", "to_package": "forbidden"}}]
        prepared = self.prepare()
        self.assertFalse(prepared["requires_response"])
        self.assertEqual(1, prepared["report"]["total_findings"])
        self.assertEqual("member/Cargo.toml", prepared["report"]["findings"][0]["evidence"]["path"])
        self.assertEqual("introduced", prepared["report"]["findings"][0]["novelty"])
        self.assertEqual("complete", prepared["report"]["status"])

    def test_cross_repository_and_publish_config_rejected(self):
        self.snapshot["config"]["repository"] = "other/repo"
        with self.assertRaisesRegex(Error, "identity"):
            self.prepare()
        self.snapshot["config"]["repository"] = "owner/repo"
        self.snapshot["config"]["publish"] = True
        with self.assertRaisesRegex(Error, "unsupported"):
            self.prepare()

    def test_policy_history_is_independent_and_target_commits_are_not_policy(self):
        self.load.side_effect = load_git_snapshot
        prepared = self.prepare()
        self.assertEqual(self.policy, prepared["task"]["trusted_sha"])
        self.assertNotIn(self.policy, self.command("rev-list", "--all").splitlines())
        for code_commit in (self.base, self.head):
            with self.subTest(code_commit=code_commit), self.assertRaisesRegex(Error, "policy commit is unavailable"):
                prepare_review(self.root, "owner/repo", self.base, self.head, code_commit,
                               memory_root=self.memory)

    def test_overlapping_policy_roots_rejected_before_reads_or_writes(self):
        before = file_tree(self.directory)
        for memory in (self.root, self.root / "policy", self.directory):
            with self.subTest(memory=memory), self.assertRaisesRegex(Error, "non-overlapping"):
                prepare_review(self.root, "owner/repo", self.base, self.head, self.policy,
                               memory_root=memory)
        self.load.assert_not_called()
        self.assertEqual(before, file_tree(self.directory))

    def test_memory_root_is_required_without_target_fallback(self):
        before = file_tree(self.root)
        with self.assertRaisesRegex(TypeError, "memory_root"):
            prepare_review(self.root, "owner/repo", self.base, self.head, self.policy)
        self.assertEqual(before, file_tree(self.root))

    def test_safe_offline_html_and_no_publishing(self):
        html = render_html({"unsafe": "</pre><script>alert('x')</script>"})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("default-src 'none'", html)
        with self.assertRaises(Error):
            publish()

    def test_hunk_line_numbers_ignore_deleted_lines(self):
        patch_text = "@@ -3,2 +3,3 @@\n-old\n+new\n+another\n context\n"
        self.assertEqual([3, 4], _added_lines(patch_text))
        self.assertEqual([1], _added_lines("@@ -1 +1 @@\n+x\u2028@@ -1 +999 @@\u2028+fake\n"))
        self.assertEqual(["a\u2028b", "c"], _source_lines("a\u2028b\r\nc\n"))


if __name__ == "__main__":
    unittest.main()
