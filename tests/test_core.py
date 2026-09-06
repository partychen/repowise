import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "skills" / "review-memory" / "scripts"))

from review_memory.common import Error, canonical_bytes, digest, load_json, load_yaml, parse_yaml, runtime_info, safe_path, write_yaml
from review_memory.core import LEGACY_NAMESPACE, NAMESPACE, approval_request, approve, initialize, load_git_snapshot, validate_knowledge


def knowledge():
    return {
        "schema_version": 1, "id": "K-example", "revision": 1, "title": "Example policy",
        "maturity": "approved", "execution": "llm", "enforcement": "advisory",
        "owner": "maintainer", "principle": "Boundary errors need caller-visible context.",
        "applicability": {"paths": ["**/*.rs"], "exclusions": ["generated/**"], "context": "Public error boundaries only."},
        "sources": [{"kind": "policy", "reference": "CONTRIBUTING.md", "version": "abc123", "available_at": "2026-01-01T00:00:00Z"}],
        "examples": {"valid": ["A private helper propagates the typed source error."],
                     "violating": ["A public boundary discards the only failure context."]},
        "exceptions": [], "detector_ref": None, "observed_at": "2026-01-01T00:00:00Z",
        "effective_from": "2026-01-01T00:00:00Z", "valid_until": None,
    }


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        initialize(self.root, "example/project")

    def test_init_idempotent_and_no_trust(self):
        self.assertEqual(initialize(self.root, "example/project")["status"], "already_initialized")
        self.assertIn("local/", (self.root / ".review" / ".gitignore").read_text())
        self.assertFalse(load_yaml(self.root / ".review" / "config.yaml")["publish"])
        with self.assertRaises(Error):
            initialize(self.root, "other/repo")

    def test_yaml_duplicates_and_tags_rejected(self):
        for text in ("id: a\nid: b", "!!python/object/apply:os.system ['echo nope']", "a: &a [*a]"):
            with self.assertRaises(Error):
                parse_yaml(text)

    def test_paths(self):
        for name in ("../secret", "a/../../secret", "C:\\secret", "\\\\server\\file", "/etc/passwd"):
            with self.assertRaises(Error):
                safe_path(self.root, name)
        self.assertEqual(safe_path(self.root, "safe/file").parent.name, "safe")

    def test_approval_rejects_insufficient_evidence(self):
        item = knowledge()
        for field, replacement in (("sources", []), ("owner", None), ("enforcement", "gate")):
            bad = copy.deepcopy(item)
            bad[field] = replacement
            with self.assertRaises(Error):
                validate_knowledge(bad, approving=True)
        item["examples"]["valid"] = []
        with self.assertRaises(Error):
            validate_knowledge(item, approving=True)

    def test_request_does_not_approve(self):
        path = self.root / "candidate.yaml"
        write_yaml(path, knowledge())
        result = approval_request(self.root, path, "knowledge", "maintainer@example.test", "Reviewed independent examples.")
        payload = load_json(Path(result["request"]))
        self.assertEqual(Path(result["request"]).read_bytes(), canonical_bytes(payload))
        self.assertEqual(payload["content_hash"], digest(knowledge()))
        self.assertEqual(list((self.root / ".review" / "knowledge").glob("*")), [])

    def test_unsigned_approval_rejected(self):
        path = self.root / "candidate.yaml"
        write_yaml(path, knowledge())
        result = approval_request(self.root, path, "knowledge", "maintainer", "Reviewed")
        signature = self.root / "invalid.sig"
        signature.write_text("not a signature")
        with self.assertRaises(Error):
            approve(self.root, Path(result["request"]), signature)

    def test_signed_approval_snapshot_and_tamper(self):
        # Ephemeral test-only keys never leave the temporary directory.
        key = self.root / "test-key"
        command = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                                 capture_output=True)
        if command.returncode:
            self.skipTest("OpenSSH key generation unavailable")
        public = key.with_suffix(".pub").read_text().strip()
        (self.root / ".review" / "allowed_signers").write_text(f"tester {public}\n")
        candidate = self.root / "candidate.yaml"
        write_yaml(candidate, knowledge())
        result = approval_request(self.root, candidate, "knowledge", "tester", "Synthetic test approval only")
        request = Path(result["request"])
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "review-memory-v1", str(request)],
                       capture_output=True, check=True)
        self.assertEqual(approve(self.root, request, Path(str(request) + ".sig"))["status"], "approved")
        self.assertEqual(approve(self.root, request, Path(str(request) + ".sig"))["status"], "already_approved")
        self._git("init", "-q")
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "Synthetic policy")
        snapshot = load_git_snapshot(self.root, "HEAD")
        self.assertEqual(len(snapshot["knowledge"]), 1)
        self.assertEqual(load_git_snapshot(self.root, "HEAD", at="2025-01-01T00:00:00Z")["knowledge"], [])
        suspended = knowledge()
        suspended.update(revision=2, maturity="needs_review")
        write_yaml(candidate, suspended)
        revision_request = Path(approval_request(self.root, candidate, "knowledge", "tester", "Counterexample requires review")["request"])
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "review-memory-v1", str(revision_request)],
                       capture_output=True, check=True)
        approve(self.root, revision_request, Path(str(revision_request) + ".sig"))
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "Suspend synthetic policy")
        suspended_snapshot = load_git_snapshot(self.root, "HEAD")
        self.assertEqual(suspended_snapshot["knowledge"], [])
        self.assertEqual(suspended_snapshot["manifest"]["coverage_gaps"][0]["knowledge_id"], "K-example")
        self.assertEqual(len(load_git_snapshot(self.root, "HEAD~1")["knowledge"]), 1)
        file = self.root / ".review" / "knowledge" / "K-example-r1.yaml"
        altered = load_yaml(file)
        altered["principle"] = "Changed after signature"
        write_yaml(file, altered)
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "Tampered")
        with self.assertRaises(Error):
            load_git_snapshot(self.root, "HEAD")

    def _git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_legacy_signature_requires_new_active_revision_after_upgrade(self):
        key = self.root / "upgrade-test-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       capture_output=True, check=True)
        (self.root / ".review" / "allowed_signers").write_text("tester " + key.with_suffix(".pub").read_text())
        candidate = self.root / "candidate.yaml"
        write_yaml(candidate, knowledge())
        request = Path(approval_request(self.root, candidate, "knowledge", "tester", "Synthetic legacy approval")["request"])
        legacy = load_json(request)
        legacy.pop("signature_namespace")
        legacy["runtime"] = dict(runtime_info(), version="0.1.0")
        request.write_bytes(canonical_bytes(legacy))
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", LEGACY_NAMESPACE, str(request)],
                       capture_output=True, check=True)
        with patch("review_memory.core.runtime_info", return_value=legacy["runtime"]):
            approve(self.root, request, Path(str(request) + ".sig"))
        self._git("init", "-q")
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test",
                  "-c", "commit.gpgsign=false", "commit", "-qm", "Legacy approved policy")
        with self.assertRaisesRegex(Error, "runtime"):
            load_git_snapshot(self.root, "HEAD")
        revision = dict(knowledge(), revision=2)
        write_yaml(candidate, revision)
        upgraded = Path(approval_request(self.root, candidate, "knowledge", "tester", "Synthetic reapproval")["request"])
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(upgraded)],
                       capture_output=True, check=True)
        approve(self.root, upgraded, Path(str(upgraded) + ".sig"))
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test",
                  "-c", "commit.gpgsign=false", "commit", "-qm", "Reapprove current runtime")
        snapshot = load_git_snapshot(self.root, "HEAD")
        self.assertEqual([2], [item["revision"] for item in snapshot["knowledge"]])


if __name__ == "__main__":
    unittest.main()
