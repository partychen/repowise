import copy
import hashlib
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from tests.test_core import knowledge
from review_memory import collect, core
from review_memory.approvals import approval_queue, prepare_approval
from review_memory.common import (
    Error, canonical_bytes, digest, load_json, load_yaml, runtime_info,
    timestamp, utcnow, write_json, write_yaml,
)
from review_memory.propose import load_saved_proposal, propose
from review_memory.sync import project_status, sync_project


PROJECT = Path(__file__).resolve().parents[1]


class DisplayOnlyPath(type(Path())):
    def resolve(self, *args, **kwargs):
        raise AssertionError("The handoff must not inspect display-only target paths.")

    stat = exists = is_dir = is_file = read_bytes = read_text = resolve


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.workspace = PROJECT / "tests" / "fixtures" / "approvals" / ("run-" + uuid.uuid4().hex)
        self.workspace.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.workspace)
        self.root = self.workspace / "external-memory"
        self.target = self.workspace / "target"
        self.target.mkdir()
        (self.target / "keep.txt").write_text("Do not modify the target.", encoding="utf-8")
        core.initialize(self.root, "example/project")
        self.bundle_path = self.root / ".review" / "local" / "raw" / "evidence" / "E-1.json"
        self.bundle = {
            "evidence_id": "E-1", "repository": "example/project", "pr": 7,
            "available_at": "2026-01-01T00:00:00Z",
            "observations": [{"body": "Preserve request context at the public boundary."}],
            "gaps": ["The implementation outcome is not established."],
        }
        write_json(self.bundle_path, self.bundle)
        evidence = [{"evidence_id": "E-1", "path": self.bundle_path.relative_to(self.root).as_posix(),
                     "content_hash": digest(self.bundle)}]
        data = {"repository": "example/project", "evidence": evidence}
        self.task = {
            "task_type": "induce", "task_id": "induce-test", "input_hash": digest(data),
            "repository": "example/project", "pr": 7, "created_at": "2026-01-01T00:00:00Z",
            "input": data, "evidence": evidence, "gaps": ["Synthetic evidence, not product accuracy."],
        }
        self.task_path = self.root / ".review" / "local" / "induction.json"
        write_json(self.task_path, self.task)

    def candidate(self, **updates):
        result = knowledge()
        result.update(maturity="candidate", owner=None, effective_from=None, sources=[], evidence_ids=["E-1"])
        result.update(updates)
        return result

    def save(self, candidate=None, task_path=None, empty=False):
        task_path = task_path or self.task_path
        task = load_json(task_path)
        candidate = candidate or self.candidate()
        candidate = copy.deepcopy(candidate)
        candidate["evidence_ids"] = [task["evidence"][0]["evidence_id"]]
        response_path = self.root / ".review" / "local" / "response.json"
        write_json(response_path, {
            "task_id": task["task_id"], "input_hash": task["input_hash"],
            "model": {"provider": "host", "model": "synthetic-test", "prompt_version": "test-v1"},
            "candidates": [] if empty else [candidate],
        })
        result = propose(self.root, task_path, response_path)
        return Path(result["proposal"]).parent / f"{candidate['id']}-r{candidate['revision']}.yaml"

    def prepare(self, path, **kwargs):
        return prepare_approval(self.root, path, "maintainer@example.test", "explicit-owner",
                                "Synthetic maintainer-supplied reason.", **kwargs)

    def reserve(self, candidate_path, revision=1, *, kind="knowledge", runtime=None):
        if kind == "knowledge":
            content = load_yaml(candidate_path)
            content.update(revision=revision, maturity="approved", owner="synthetic-maintainer",
                           effective_from="2026-01-01T00:00:00Z")
            group = "knowledge"
        else:
            content = {
                "schema_version": 1, "id": "D-boundary", "revision": revision,
                "tool_id": "rust.forbidden-dependency.v1",
                "parameters": {"from_package": "api", "to_package": "implementation"},
                "owner": "synthetic-maintainer", "validation_ref": "synthetic-fixture",
                "valid_examples": ["api depends on interface"],
                "violating_examples": ["api depends on implementation"],
            }
            group = "detectors"
        key = f"{content['id']}-r{revision}"
        content_path = self.root / ".review" / group / f"{key}.yaml"
        record_path = self.root / ".review" / "approvals" / f"{key}.json"
        write_yaml(content_path, content)
        payload = {
            "schema_version": 1, "repository": "example/project", "kind": kind,
            "identity": "synthetic-maintainer", "reason": "Unverified test reservation only.",
            "requested_at": utcnow(), "content": content, "content_hash": digest(content),
            "runtime": runtime_info() if runtime is None else runtime, "signature_namespace": core.NAMESPACE,
        }
        write_json(record_path, {
            "payload": payload, "signature": "synthetic-invalid-signature-not-an-approval", "recorded_at": utcnow(),
        })
        return content_path, record_path

    def test_queue_is_readable_evidence_bound_and_needs_no_signer_setup(self):
        candidate_path = self.save()
        write_yaml(self.root / ".review" / "local" / "proposals" / "loose.yaml", knowledge())
        write_json(self.root / ".review" / "local" / "learning" / "index.json",
                   {"authority": "approved", "entries": [{"id": "K-forged-index"}]})
        with patch("subprocess.run", side_effect=AssertionError("No subprocess is authorized.")):
            result = approval_queue(self.root)
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["signer_trust"]["status"], "absent")
        self.assertFalse(result["signer_trust"]["learning_prerequisite"])
        entry = result["candidates"][0]
        self.assertEqual(entry["candidate_path"], str(candidate_path))
        self.assertTrue(entry["can_prepare_request"])
        self.assertEqual(entry["authority"], "unapproved")
        self.assertEqual(entry["maturity"], "candidate")
        self.assertEqual(entry["source_prs"][0]["url"], "https://github.com/example/project/pull/7")
        self.assertTrue(entry["coverage_gaps"])
        self.assertEqual(entry["adjudication"], "unjudged")
        preview = Path(result["preview_path"]).read_text(encoding="utf-8")
        for text in ("Principle", "Applicability", "Exceptions", "Valid examples", "Violating examples",
                     "PR #7", "Original candidate YAML", "Untrusted source quotation", "Readiness blockers"):
            self.assertIn(text, preview)
        self.assertEqual(load_json(Path(result["queue_path"])), result)
        self.assertFalse(list((self.root / ".review" / "knowledge").glob("*.yaml")))

    def test_missing_signer_file_does_not_prevent_unsigned_preparation(self):
        path = self.save()
        (self.root / ".review" / "allowed_signers").unlink()
        result = self.prepare(path)
        self.assertEqual(result["signer_trust"]["status"], "absent")
        self.assertFalse((self.root / ".review" / "allowed_signers").exists())

    def test_prepare_preserves_candidate_and_core_canonical_request_bytes(self):
        path = self.save()
        original = path.read_bytes()
        before = utcnow()
        with patch("subprocess.run", side_effect=AssertionError("No subprocess is authorized.")), \
                patch.object(core, "approve", side_effect=AssertionError("Do not auto-approve.")), \
                patch.object(core, "verify_signature", side_effect=AssertionError("Do not invoke ssh-keygen.")), \
                patch.object(core, "approval_request", wraps=core.approval_request) as request:
            result = self.prepare(path)
        after = utcnow()
        request.assert_called_once_with(self.root.resolve(), Path(result["reviewed_path"]), "knowledge",
                                        "maintainer@example.test", "Synthetic maintainer-supplied reason.")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result["candidate_file_hash"], hashlib.sha256(original).hexdigest())
        reviewed = load_yaml(Path(result["reviewed_path"]))
        expected = load_yaml(path)
        expected.update(maturity="approved", owner="explicit-owner", effective_from=result["effective_from"])
        self.assertEqual(reviewed, expected)
        self.assertGreaterEqual(timestamp(result["effective_from"]), timestamp(before))
        self.assertLessEqual(timestamp(result["effective_from"]), timestamp(after))
        payload = load_json(Path(result["request"]))
        self.assertEqual(Path(result["request"]).read_bytes(), canonical_bytes(payload))
        self.assertEqual(payload["content"], reviewed)
        self.assertEqual(payload["kind"], "knowledge")
        self.assertEqual(payload["content_hash"], digest(reviewed))
        self.assertEqual(result["request_hash"], digest(payload))
        self.assertEqual(result["status"], "awaiting_maintainer_signature")
        self.assertFalse(result["signed"])
        self.assertFalse(result["active"])
        self.assertEqual(load_json(Path(result["handoff_path"])), result)
        self.assertTrue(Path(result["preview_path"]).is_file())
        self.assertFalse(list((self.root / ".review" / "knowledge").glob("*")))
        self.assertFalse(list((self.root / ".review" / "approvals").glob("*")))
        second = self.prepare(path)
        self.assertNotEqual(second["reviewed_path"], result["reviewed_path"])
        self.assertEqual(path.read_bytes(), original)

    def test_optional_target_and_data_home_are_display_only_and_handoff_is_external(self):
        path = self.save()
        target = DisplayOnlyPath(self.target / "uninspected 'target")
        data_home = DisplayOnlyPath(self.workspace / "uninspected data home")
        before = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("No subprocess is authorized.")):
            queue = approval_queue(self.root, target_root=target, data_home=data_home)
            result = self.prepare(path, target_root=target, data_home=data_home)
        after = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.target / ".review").exists())
        command = queue["candidates"][0]["prepare_command"]
        self.assertIn("--root '" + str(target).replace("'", "''") + "'", command)
        self.assertIn("--data-home '" + str(data_home) + "'", command)
        steps = {step["id"]: step for step in result["human_steps"]}
        self.assertTrue(all(step["actor"] == "human" for step in steps.values()))
        self.assertIn("approve --request", steps["import_signature"]["command"])
        self.assertIn("--signature", steps["import_signature"]["command"])
        self.assertEqual(steps["policy_history"]["storage_root"], str(self.root.resolve()))
        self.assertIn("never the target", result["policy_history_note"])
        self.assertEqual(steps["signer_trust"]["path"], str(self.root / ".review" / "allowed_signers"))
        self.assertIn("$MaintainerKey", steps["sign"]["command"])
        self.assertIn("text 'yes'", steps["inspect"]["instruction"])

    def test_saved_content_changes_are_visible_gaps_and_cannot_be_prepared(self):
        path = self.save()
        manifest_path = path.parent / "proposal.json"
        mutations = [
            (manifest_path, lambda x: x.update(response_hash="forged")),
            (manifest_path, lambda x: x["candidates"][0]["knowledge"].update(title="forged")),
            (manifest_path, lambda x: x.update(model={"provider": "forged"})),
            (manifest_path, lambda x: x.update(coverage_gaps=[])),
            (path.parent / "response.json", lambda x: x["candidates"][0].update(principle="forged")),
            (path.parent / "task.json", lambda x: x.update(task_id="forged")),
            (self.bundle_path, lambda x: x.update(pr=99)),
        ]
        for changed, mutate in mutations:
            with self.subTest(path=changed, mutation=mutate):
                original = changed.read_bytes()
                data = load_json(changed)
                mutate(data)
                write_json(changed, data)
                try:
                    queue = approval_queue(self.root)
                    self.assertEqual(queue["status"], "partial")
                    self.assertEqual(queue["candidate_count"], 0)
                    self.assertTrue(queue["coverage_gaps"])
                    with self.assertRaises(Error):
                        self.prepare(path)
                finally:
                    changed.write_bytes(original)
        original = path.read_bytes()
        data = load_yaml(path)
        data["principle"] = "Tampered after the proposal."
        write_yaml(path, data)
        with self.assertRaisesRegex(Error, "candidate file"):
            self.prepare(path)
        self.assertEqual(approval_queue(self.root)["status"], "partial")
        path.write_bytes(original)
        self.assertFalse(list((self.root / ".review" / "local" / "approvals").glob("preparation-*")))

    def test_normalized_knowledge_is_bound_and_actual_candidate_bytes_are_reported(self):
        path = self.save()
        path.write_bytes(path.read_bytes() + b"\n# Human formatting only, no content changes.\n")
        proposal = load_saved_proposal(self.root, path.parent / "proposal.json")
        self.assertEqual(proposal["candidates"][0]["file_hash"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(proposal["candidates"][0]["content_hash"], digest(load_yaml(path)))
        self.assertEqual(self.prepare(path)["candidate_file_hash"], proposal["candidates"][0]["file_hash"])

    def test_loose_or_other_store_candidate_is_rejected_even_with_identical_content(self):
        path = self.save()
        copied = path.parent / "unbound-copy.yaml"
        copied.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(Error, "does not belong"):
            self.prepare(copied)
        external = self.target / "candidate.yaml"
        external.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(Error, "storage root"):
            self.prepare(external)
        with self.assertRaises(Error):
            self.prepare(Path("..") / "candidate.yaml")
        self.assertTrue(self.prepare(path.relative_to(self.root))["request"])

    def test_missing_manifest_or_evidence_never_looks_like_an_empty_success(self):
        path = self.save()
        self.bundle_path.unlink()
        queue = approval_queue(self.root)
        self.assertEqual(queue["status"], "partial")
        self.assertTrue(queue["coverage_gaps"])
        with self.assertRaises(Error):
            self.prepare(path)
        write_json(self.bundle_path, self.bundle)
        (path.parent / "proposal.json").unlink()
        queue = approval_queue(self.root)
        self.assertEqual(queue["status"], "partial")
        self.assertTrue(queue["coverage_gaps"])

    def test_linked_proposal_or_evidence_is_rejected_before_handoff(self):
        path = self.save()
        for refused_path in (path.parent, self.bundle_path):
            with self.subTest(path=refused_path), \
                    patch("review_memory.common.is_link", side_effect=lambda entry: entry == refused_path):
                queue = approval_queue(self.root)
                self.assertEqual(queue["status"], "partial")
                self.assertEqual(queue["candidate_count"], 0)
                self.assertIn("Symlinks and junctions", queue["coverage_gaps"][0]["reason"])
                with self.assertRaisesRegex(Error, "Symlinks and junctions"):
                    self.prepare(path)

    def test_missing_examples_are_blockers_not_fabricated_content(self):
        for field in ("valid", "violating"):
            with self.subTest(field=field):
                candidate = self.candidate(id="K-missing-" + field)
                candidate["examples"][field] = []
                path = self.save(candidate)
                entry = next(row for row in approval_queue(self.root)["candidates"]
                             if row["candidate_path"] == str(path))
                self.assertFalse(entry["can_prepare_request"])
                self.assertIn(f"missing_{field}_examples", [x["code"] for x in entry["readiness_blockers"]])
                with self.assertRaisesRegex(Error, "examples"):
                    self.prepare(path)
                self.assertEqual(load_yaml(path)["examples"][field], [])

    def test_expired_candidate_cannot_be_made_ready_by_backdating(self):
        path = self.save(self.candidate(valid_until="2026-02-01T00:00:00Z"))
        with patch("review_memory.approvals.utcnow", return_value="2026-03-01T00:00:00Z"):
            entry = approval_queue(self.root)["candidates"][0]
            self.assertFalse(entry["can_prepare_request"])
            self.assertIn("expired_candidate", [x["code"] for x in entry["readiness_blockers"]])
            with self.assertRaisesRegex(Error, "expired"):
                self.prepare(path, effective_from="2026-01-01T00:00:00Z")

    def test_explicit_required_inputs_and_revision_are_validated_before_artifacts(self):
        path = self.save()
        for identity, owner, reason in (("", "owner", "reason"), ("person", "", "reason"),
                                        ("person", "owner", " \n"), ("gh inferred identity", "owner", "reason")):
            with self.subTest(identity=identity, owner=owner, reason=reason), self.assertRaises(Error):
                prepare_approval(self.root, path, identity, owner, reason)
        for revision in (0, -1, True, "2", 1.5):
            with self.subTest(revision=revision), self.assertRaises(Error):
                self.prepare(path, revision=revision)
        with self.assertRaises(Error):
            self.prepare(path, effective_from="2026-01-01")
        self.assertFalse((self.root / ".review" / "local" / "approvals").exists())

    def test_recorded_revisions_remain_unverified_reserved_and_unchanged(self):
        path = self.save()
        content_path, record_path = self.reserve(path, revision=2, runtime={"version": "old-runtime"})
        before = {p: p.read_bytes() for p in (path, content_path, record_path)}
        with patch("subprocess.run", side_effect=AssertionError("No signature verification here.")):
            queue = approval_queue(self.root)
            entry = queue["candidates"][0]
            self.assertEqual(entry["authority"], "unapproved")
            self.assertFalse(entry["can_prepare_request"])
            state = entry["recorded_approval"]
            self.assertEqual(state["max_used_revision"], 2)
            self.assertEqual(state["active"], "not_evaluated")
            self.assertFalse(state["revisions"][0]["signature_verified"])
            self.assertFalse(state["revisions"][0]["runtime_matches"])
            self.assertEqual(entry["suggested_revision"], 3)
            self.assertIn("--revision $Revision", entry["prepare_command"])
            for revision in (None, 1, 2):
                with self.subTest(revision=revision), self.assertRaisesRegex(Error, "already-used"):
                    self.prepare(path, revision=revision)
            prepared = self.prepare(path, revision=3)
        self.assertEqual(prepared["revision"], 3)
        self.assertEqual(prepared["original_revision"], 1)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertIn("exact trusted implementation", prepared["runtime_note"])

    def test_higher_candidate_revision_remains_pending_despite_older_records(self):
        first = self.save()
        self.reserve(first)
        second = self.save(self.candidate(revision=2))
        queue = approval_queue(self.root)
        self.assertEqual(queue["pending_count"], 1)
        self.assertEqual(queue["previously_recorded_count"], 1)
        entry = next(item for item in queue["candidates"] if item["candidate_path"] == str(second))
        self.assertTrue(entry["can_prepare_request"])
        self.assertEqual(self.prepare(second)["revision"], 2)

    def test_partial_approval_is_an_explicit_inspection_blocker(self):
        path = self.save()
        content_path, record_path = self.reserve(path)
        content_path.unlink()
        before = record_path.read_bytes()
        entry = approval_queue(self.root)["candidates"][0]
        self.assertTrue(entry["recorded_approval"]["errors"])
        with self.assertRaisesRegex(Error, "Partial approval"):
            self.prepare(path, revision=2)
        self.assertEqual(record_path.read_bytes(), before)

    def test_detector_authorization_is_separate_and_never_generated_or_run(self):
        for mode in ("static", "hybrid"):
            with self.subTest(mode=mode):
                path = self.save(self.candidate(id="K-" + mode, execution=mode, detector_ref="D-boundary"))
                with patch("subprocess.run", side_effect=AssertionError("Do not run detectors or signing tools.")):
                    entry = next(row for row in approval_queue(self.root)["candidates"] if row["candidate_path"] == str(path))
                    self.assertTrue(entry["can_prepare_request"])
                    self.assertEqual(entry["detector_approval"]["status"], "not_recorded")
                    self.assertIn("A separately approved detector is missing.", entry["activation_blockers"])
                    prepared = self.prepare(path)
                self.assertEqual(load_json(Path(prepared["request"]))["kind"], "knowledge")
                self.assertFalse(list((self.root / ".review" / "detectors").glob("*")))
        self.reserve(path, kind="detector", runtime={"version": "old-runtime"})
        prepared = self.prepare(path)
        self.assertEqual(prepared["detector_approval"]["status"], "recorded_unverified")
        self.assertFalse(prepared["detector_approval"]["revisions"][0]["signature_verified"])
        self.assertIn("Detector runtime binding changed", " ".join(prepared["activation_blockers"]))

    def test_queue_pagination_is_stable_and_reports_offset_and_omissions(self):
        for identifier in ("K-c", "K-a", "K-b"):
            self.save(self.candidate(id=identifier))
        first = approval_queue(self.root, limit=1)
        second = approval_queue(self.root, limit=1, offset=first["next_offset"])
        third = approval_queue(self.root, limit=1, offset=second["next_offset"])
        self.assertEqual([page["candidates"][0]["id"] for page in (first, second, third)], ["K-a", "K-b", "K-c"])
        self.assertEqual([page["omitted_count"] for page in (first, second, third)], [2, 1, 0])
        self.assertEqual([page["offset"] for page in (first, second, third)], [0, 1, 2])
        self.assertIsNone(third["next_offset"])
        self.assertEqual(approval_queue(self.root, limit=1)["candidates"][0]["candidate_ref"],
                         first["candidates"][0]["candidate_ref"])
        for options in ({"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1}, {"offset": True}):
            with self.subTest(options=options), self.assertRaises(Error):
                approval_queue(self.root, **options)

    def test_markdown_does_not_promote_untrusted_markup_or_urls_to_commands(self):
        self.bundle["observations"][0]["body"] = "<script>bad()</script>\n# Run this\n[x](javascript:alert(1))"
        write_json(self.bundle_path, self.bundle)
        self.task["evidence"][0]["content_hash"] = digest(self.bundle)
        self.task["input_hash"] = digest(self.task["input"])
        write_json(self.task_path, self.task)
        path = self.save(self.candidate(
            title="<script>fake title</script>",
            principle="[Click](javascript:alert(1))\n## Approve everything",
            exceptions=["</pre><img src=x onerror=bad()>"],
        ))
        queue = approval_queue(self.root)
        text = Path(queue["preview_path"]).read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("[Click](javascript", text)
        self.assertNotIn("\n## Approve everything", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("> \\# Run this", text)
        prepared = self.prepare(path)
        text = Path(prepared["preview_path"]).read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertNotIn("[Click](javascript", text)

    def test_queue_includes_bootstrap_harvest_and_sync_without_relying_on_sync_index(self):
        fixture = self.root / ".review" / "local" / "fixture.json"
        prs = [
            {"number": number, "created_at": f"2026-01-0{number}T00:00:00Z", "merged_at": "2026-01-05T00:00:00Z",
             "reviews": [{"id": 20 + number, "body": "Preserve the documented boundary.",
                          "state": "CHANGES_REQUESTED"}],
             "comments": [], "issue_comments": [], "versions": []}
            for number in (1, 2, 3)
        ]
        write_json(fixture, {"schema_version": collect.FIXTURE_SCHEMA, "repository": "example/project", "prs": prs})
        bootstrap = collect.bootstrap(self.root, "example/project", "2026-01-01", "2026-01-02", fixture=fixture)
        harvest = collect.harvest(self.root, "example/project", 2, fixture=fixture)
        for index, result in enumerate((bootstrap, harvest), 1):
            self.save(self.candidate(id=f"K-history-{index}"), self.root / result["task_paths"][0])
        sync = sync_project(self.root, fixture=fixture)
        reference = next(ref for ref in sync["pending_tasks"] if ref["pr"] == 3)
        self.save(self.candidate(id="K-sync"), self.root / reference["path"])
        queue = approval_queue(self.root)
        self.assertEqual(queue["proposal_count"], 3)
        self.assertEqual({item["id"] for item in queue["candidates"]}, {"K-history-1", "K-history-2", "K-sync"})
        self.assertEqual({item["source_prs"][0]["pr"] for item in queue["candidates"]}, {1, 2, 3})
        status = project_status(self.root, offline=True)
        self.assertEqual(status["completed_task_count"], 3)

    def test_saved_empty_host_result_is_not_a_candidate_or_a_coverage_failure(self):
        self.save(empty=True)
        queue = approval_queue(self.root)
        self.assertEqual(queue["status"], "ready")
        self.assertEqual(queue["proposal_count"], 1)
        self.assertEqual(queue["candidate_count"], 0)
        self.assertEqual(queue["coverage_gaps"], [])


if __name__ == "__main__":
    unittest.main()
