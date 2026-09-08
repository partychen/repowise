import copy
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SKILL = PROJECT / ".github" / "skills" / "repowise"
sys.path.insert(0, str(SKILL / "scripts"))

from repowise.common import Error, canonical_bytes, digest, load_json, load_yaml, parse_yaml, runtime_info, safe_path, write_yaml
from repowise.core import (
    NAMESPACE, approval_request, approve, initialize, load_git_snapshot,
    render_snapshot, validate_knowledge, verify_signature,
)


@contextlib.contextmanager
def fixture_directory():
    with tempfile.TemporaryDirectory(prefix=".test-repowise-", dir=PROJECT) as directory:
        # Disposable sibling repositories stay project-local; the real Skill remains protected.
        with patch("repowise.storage._protected_roots", return_value=[SKILL]):
            yield Path(directory).resolve()


def git_command(root, *args, at=None):
    env = dict(os.environ)
    if at is not None:
        env.update(GIT_AUTHOR_DATE=at, GIT_COMMITTER_DATE=at)
    return subprocess.run(["git", "-C", str(root), "--no-pager", *args],
                          env=env, check=True, capture_output=True).stdout.decode("utf-8").strip()


def initialize_git(root):
    root.mkdir(parents=True, exist_ok=True)
    git_command(root, "init", "--quiet")
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Synthetic fixture"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false"),
                       ("core.hooksPath", str(root / "no-hooks"))):
        git_command(root, "config", key, value)


def file_tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}


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
        self.directory = self.enterContext(fixture_directory())
        self.root = self.directory / "policy"
        self.target = self.directory / "code"
        initialize_git(self.target)
        initialize(self.root, "example/project")

    def test_init_idempotent_and_no_trust(self):
        self.assertEqual(initialize(self.root, "example/project")["status"], "already_initialized")
        self.assertIn("local/", (self.root / ".review" / ".gitignore").read_text())
        self.assertIn("project.json", (self.root / ".review" / ".gitignore").read_text())
        self.assertFalse(load_yaml(self.root / ".review" / "config.yaml")["publish"])
        self.assertFalse((self.root / ".git").exists())
        self.assertFalse((self.target / ".review").exists())
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

    def test_knowledge_globs_do_not_access_the_filesystem(self):
        item = knowledge()
        item["applicability"]["paths"] = ["**/*.rs", "src/handler?.rs", "crates/[ab]*/**", "Cargo.toml"]
        item["applicability"]["exclusions"] = ["generated/**", "**/test[0-9].rs"]
        with patch.object(Path, "cwd", side_effect=AssertionError("Patterns must not depend on cwd")), \
                patch.object(Path, "lstat", side_effect=OSError(123, "Invalid Windows filename")), \
                patch.object(Path, "resolve", side_effect=AssertionError("Patterns are not filesystem paths")):
            self.assertEqual(validate_knowledge(item, approving=True), item)

    def test_knowledge_globs_reject_path_escapes(self):
        for field in ("paths", "exclusions"):
            for pattern in ("../*.rs", "src/../../*", "src\\..\\*", "/**", "\\**",
                            "C:\\*.rs", "C:*.rs", "\\\\server\\share\\*", "src/\x00*",
                            "", " ", None, 7):
                with self.subTest(field=field, pattern=pattern):
                    item = knowledge()
                    item["applicability"][field] = [pattern]
                    with self.assertRaises(Error):
                        validate_knowledge(item)

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
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "repowise-v1", str(request)],
                       capture_output=True, check=True)
        before = file_tree(self.target)
        approved = approve(self.root, request, Path(str(request) + ".sig"))
        self.assertEqual(approved["status"], "approved")
        self.assertIn("external memory/policy repository", approved["note"])
        self.assertEqual(approve(self.root, request, Path(str(request) + ".sig"))["status"], "already_approved")
        initialize_git(self.root)
        (self.root / ".review" / "project.json").write_text('{"target_root": "private-binding"}')
        self._git("add", ".review")
        self._git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "Synthetic policy")
        self.assertEqual("", self._git("ls-files", ".review/project.json", ".review/local"))
        snapshot = load_git_snapshot(self.root, "HEAD")
        self.assertEqual(len(snapshot["knowledge"]), 1)
        self.assertEqual("external_memory", snapshot["manifest"]["policy_source"])
        self.assertEqual(1, render_snapshot(self.root, "HEAD")["knowledge_count"])
        self.assertEqual(before, file_tree(self.target))
        self.assertEqual([], list((self.root / ".review" / "local" / "cache").iterdir()))
        self.assertEqual(load_git_snapshot(self.root, "HEAD", at="2025-01-01T00:00:00Z")["knowledge"], [])
        suspended = knowledge()
        suspended.update(revision=2, maturity="needs_review")
        write_yaml(candidate, suspended)
        revision_request = Path(approval_request(self.root, candidate, "knowledge", "tester", "Counterexample requires review")["request"])
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", "repowise-v1", str(revision_request)],
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
        return git_command(self.root, *args)

    def test_external_policy_requires_its_own_git_history(self):
        before = file_tree(self.root)
        with self.assertRaisesRegex(Error, "maintainer must initialize"):
            load_git_snapshot(self.root, "HEAD")
        self.assertEqual(before, file_tree(self.root))
        initialize_git(self.root)
        with self.assertRaisesRegex(Error, "policy commit is unavailable"):
            load_git_snapshot(self.root, "HEAD")
        self._git("add", ".review")
        self._git("commit", "-qm", "Synthetic policy store")
        nested = self.root / "nested"
        initialize(nested, "example/project")
        before = file_tree(nested)
        with self.assertRaisesRegex(Error, "own Git toplevel"):
            load_git_snapshot(nested, "HEAD")
        self.assertEqual(before, file_tree(nested))

    def test_missing_committed_external_configuration_is_actionable(self):
        initialize_git(self.root)
        self._git("commit", "--allow-empty", "-qm", "No policy history")
        with self.assertRaisesRegex(Error, "maintainer must commit"):
            load_git_snapshot(self.root, "HEAD")

    def test_policy_cannot_be_a_linked_target_worktree(self):
        git_command(self.target, "commit", "--allow-empty", "-qm", "Synthetic code history")
        linked = self.directory / "linked-policy"
        git_command(self.target, "worktree", "add", "--detach", str(linked), "HEAD")
        initialize(linked, "example/project")
        before = file_tree(self.target)
        with self.assertRaisesRegex(Error, "linked worktrees.*not independent"):
            load_git_snapshot(linked, "HEAD")
        self.assertEqual(before, file_tree(self.target))

    def test_signature_namespace_is_explicit_and_current(self):
        candidate = self.root / "candidate.yaml"
        write_yaml(candidate, knowledge())
        request = Path(approval_request(self.root, candidate, "knowledge", "tester",
                                        "Synthetic namespace boundary")["request"])
        payload = load_json(request)
        self.assertEqual(NAMESPACE, payload["signature_namespace"])
        variants = [dict(payload, signature_namespace="repo-constitution-v1"),
                    {key: value for key, value in payload.items() if key != "signature_namespace"}]
        with patch("repowise.core.subprocess.run") as process:
            for invalid in variants:
                with self.subTest(payload=invalid), self.assertRaisesRegex(Error, "signature namespace"):
                    verify_signature(invalid, "", "", root=self.root)
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
