import json
import unittest
from pathlib import PurePosixPath
from unittest.mock import patch

from tests.test_core import file_tree, fixture_directory, git_command, initialize_git
from review_memory import repository_context
from review_memory.common import Error
from review_memory.repository_context import _added_lines, _path, _source_lines, capture_context


class RepositoryContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(fixture_directory())
        self.root = self.directory / "target"
        initialize_git(self.root)

    def write(self, files):
        for name, content in files.items():
            path = self.root.joinpath(*PurePosixPath(name).parts)
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self, message="Synthetic context fixture"):
        git_command(self.root, "add", "--all")
        git_command(self.root, "commit", "--quiet", "-m", message)
        return git_command(self.root, "rev-parse", "HEAD")

    def versions(self, before, after):
        self.write(before)
        self.base = self.commit()
        self.write(after)
        self.head = self.commit()

    def record(self, path="src/change.rs", status="M", old_path=None):
        return {"path": path, "old_path": old_path or path, "status": status}

    def capture(self, records=None, requested=None, max_files=64, max_bytes=100_000, config=None):
        return capture_context(
            self.root, self.base, self.head,
            [self.record()] if records is None else records, max_files, max_bytes,
            {} if config is None else config, context_paths=requested,
        )

    def oid(self, revision, path):
        return git_command(self.root, "rev-parse", f"{revision}:{path}")

    def read_oids(self, spy):
        return [call.args[call.args.index("cat-file") + 2]
                for call in spy.call_args_list if "cat-file" in call.args]

    def test_collects_base_module_sibling_test_config_and_documentation_examples(self):
        changed = "crates/gateway/src/http/request.rs"
        far = "legacy/transport/adapter.rs"
        before = {
            "Cargo.toml": '[workspace]\nmembers = ["crates/gateway"]\n',
            "crates/gateway/Cargo.toml": '[package]\nname = "gateway"\nversion = "0.1.0"\n',
            "crates/gateway/src/lib.rs": "pub mod http;\npub mod service;\n",
            "crates/gateway/src/http/mod.rs": "pub mod request;\npub mod response;\n",
            changed: "use crate::service::Client;\nuse super::response::Reply;\npub fn handle() -> u8 { 1 }\n",
            "crates/gateway/src/http/response.rs": "pub struct Reply;\n",
            "crates/gateway/src/http/sibling.rs": "pub fn handle_other() -> u8 { 1 }\n",
            "crates/gateway/src/service.rs": "pub struct Client;\n",
            "crates/gateway/tests/request.rs": "#[test]\nfn request_contract() {}\n",
            "crates/gateway/tests/smoke.rs": "#[test]\nfn smoke() {}\n",
            "crates/gateway/rust-toolchain.toml": '[toolchain]\nchannel = "stable"\n',
            "crates/gateway/.cargo/config.toml": '[build]\ntarget-dir = "out"\n',
            "crates/gateway/rustfmt.toml": "max_width = 100\n",
            "crates/gateway/README.md": "# Gateway\nUse the existing transport boundary.\n",
            "crates/gateway/docs/architecture.md": "# Architecture\nShared client, typed replies.\n",
            "README.md": "# Synthetic workspace\n",
            ".editorconfig": "root = true\n",
            far: "pub fn handle_legacy_request() -> u8 { 1 }\n",
        }
        self.versions(before, {changed: before[changed].replace("{ 1 }", "{ 2 }")})
        contexts, gaps, consumed, selection = self.capture(
            [self.record(changed)], [far],
        )
        by_path = {item["path"]: item for item in contexts}
        self.assertEqual(set(by_path), set(before))
        self.assertEqual(gaps, [])
        self.assertEqual(by_path[changed]["changed_lines"], [3])
        self.assertEqual(selection["selected_paths"][:2], [changed, far])
        for name in set(before) - {changed}:
            with self.subTest(path=name):
                self.assertEqual(by_path[name]["status"], "context")
                self.assertEqual(by_path[name]["before"], before[name])
                self.assertEqual(by_path[name]["after"], before[name])
                self.assertEqual(by_path[name]["changed_lines"], [])
        self.assertTrue(any("Rust relationship" in r for r in by_path["crates/gateway/src/service.rs"]["selection_reasons"]))
        self.assertEqual(consumed, sum(len(item[side].encode("utf-8"))
                                       for item in contexts for side in ("before", "after")))
        self.assertEqual(selection["requested_paths"], [far])
        self.assertEqual(selection["omitted_count"], 0)
        self.assertIn("not approved policy", " ".join(selection["limitations"]))
        self.assertEqual(json.loads(json.dumps(selection)), selection)

    def test_dirty_target_files_and_git_metadata_remain_byte_for_byte_unchanged(self):
        before = {
            "src/change.rs": "pub fn value() -> u8 { 1 }\n",
            "src/sibling.rs": "pub fn sibling() -> u8 { 3 }\n",
            "Cargo.toml": '[package]\nname = "fixture"\nversion = "0.1.0"\n',
        }
        after = {
            "src/change.rs": "pub fn value() -> u8 { 2 }\n",
            "src/sibling.rs": "pub fn sibling() -> u8 { 4 }\n",
        }
        self.versions(before, after)
        self.write({
            "src/change.rs": "DIRTY worktree source, not review evidence\n",
            "src/sibling.rs": "STAGED worktree source, not review evidence\n",
            "Cargo.toml": "DIRTY invalid manifest\n",
            "src/worktree_only.rs": "pub fn not_a_precedent() {}\n",
            ".gitattributes": "* -diff\n",
        })
        git_command(self.root, "add", "--", "src/sibling.rs")
        git_command(self.root, "config", "diff.external", "collector-must-not-run-project-commands")
        git_command(self.root, "config", "color.ui", "always")
        snapshot = file_tree(self.root)
        contexts, gaps, _, _ = self.capture()
        self.assertEqual(snapshot, file_tree(self.root))
        self.assertEqual(gaps, [])
        by_path = {item["path"]: item for item in contexts}
        for name in before:
            self.assertEqual(by_path[name]["before"], before[name])
            self.assertEqual(by_path[name]["after"], after.get(name, before[name]))
        self.assertNotIn("src/worktree_only.rs", by_path)
        self.assertEqual(by_path["src/change.rs"]["changed_lines"], [1])

    def test_supplemental_deleted_and_head_only_files_keep_context_status(self):
        self.versions({
            "src/change.rs": "fn value() { old(); }\n",
            "src/base_sample.rs": "fn precedent() {}\n",
            "src/deleted.rs": "fn removed() {}\n",
        }, {
            "src/change.rs": "fn value() { new(); }\n",
            "src/base_sample.rs": None,
            "src/deleted.rs": None,
            "src/head_sample.rs": "fn new_example() {}\n",
            "src/added.rs": "fn added() {}\n// second line\n",
        })
        contexts, gaps, _, selection = self.capture(
            [self.record(), self.record("src/added.rs", "A"), self.record("src/deleted.rs", "D")],
            ["src/base_sample.rs", "src/head_sample.rs"],
        )
        by_path = {item["path"]: item for item in contexts}
        self.assertEqual(gaps, [])
        base_sample, head_sample = by_path["src/base_sample.rs"], by_path["src/head_sample.rs"]
        self.assertEqual(base_sample["before"], "fn precedent() {}\n")
        self.assertIsNone(base_sample["after"])
        self.assertIsNone(head_sample["before"])
        self.assertIsNone(head_sample["before_blob"])
        self.assertEqual(head_sample["after"], "fn new_example() {}\n")
        for item in (base_sample, head_sample):
            self.assertEqual(item["status"], "context")
            self.assertEqual(item["changed_lines"], [])
        self.assertEqual(by_path["src/added.rs"]["changed_lines"], [1, 2])
        self.assertIsNone(by_path["src/added.rs"]["before"])
        self.assertIsNone(by_path["src/deleted.rs"]["after"])
        self.assertEqual(by_path["src/deleted.rs"]["changed_lines"], [])
        selected = {item["path"]: item for item in selection["selected"]}
        self.assertEqual(selected["src/base_sample.rs"]["captured_versions"], ["BASE"])
        self.assertEqual(selected["src/head_sample.rs"]["captured_versions"], ["HEAD"])
        self.assertEqual(selection["omitted_count"], 0)

    def test_rename_preserves_old_path_and_deduplicates_requested_aliases(self):
        old = "fn unchanged() {}\nfn value() { old(); }\n"
        new = "fn unchanged() {}\nfn value() { new(); }\n"
        self.versions({"src/old.rs": old}, {"src/old.rs": None, "src/new.rs": new})
        contexts, gaps, consumed, selection = self.capture(
            [self.record("src/new.rs", "R075", "src/old.rs")],
            ["src/new.rs", "src/old.rs", "src/new.rs"], max_files=1,
        )
        self.assertEqual(gaps, [])
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["path"], "src/new.rs")
        self.assertEqual(contexts[0]["old_path"], "src/old.rs")
        self.assertEqual(contexts[0]["before"], old)
        self.assertEqual(contexts[0]["after"], new)
        self.assertEqual(contexts[0]["changed_lines"], [2])
        self.assertEqual(consumed, len(old.encode()) + len(new.encode()))
        self.assertEqual(selection["requested_paths"], ["src/new.rs", "src/old.rs"])
        self.assertEqual(selection["selected_paths"], ["src/new.rs"])
        self.assertIn("explicit requested path: src/old.rs", contexts[0]["selection_reasons"])

    def test_generated_or_excluded_either_rename_side_is_never_read(self):
        self.versions({
            "generated/old.rs": "fn generated_old() {}\n",
            "src/allowed.rs": "fn allowed_old() {}\n",
            "generated/sample.rs": "fn generated_sample() {}\n",
            "vendor/sample.rs": "fn excluded_sample() {}\n",
            "vendor/Cargo.toml": '[package]\nname = "excluded"\n',
        }, {
            "generated/old.rs": None,
            "src/new.rs": "fn generated_old() {}\n",
            "src/allowed.rs": None,
            "vendor/new.rs": "fn allowed_old() {}\n",
        })
        records = [self.record("src/new.rs", "R100", "generated/old.rs"),
                   self.record("vendor/new.rs", "R100", "src/allowed.rs")]
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, gaps, consumed, selection = self.capture(
                records, ["generated/sample.rs", "vendor/sample.rs"],
                config={"generated_paths": ["generated/**"], "exclusions": ["vendor/**"]},
            )
        self.assertEqual(contexts, [])
        self.assertEqual(consumed, 0)
        self.assertEqual(self.read_oids(spy), [])
        self.assertEqual(selection["omitted_count"], 5)
        for name in ("src/new.rs", "vendor/new.rs", "generated/sample.rs",
                     "vendor/sample.rs", "vendor/Cargo.toml"):
            self.assertTrue(any("Excluded/generated" in gap and name in gap for gap in gaps), name)

    def test_changed_requested_and_all_workspace_cargo_manifests_precede_examples(self):
        manifests = {
            "Cargo.toml": '[workspace]\nmembers = ["crates/a", "elsewhere/b"]\n',
            "crates/a/Cargo.toml": '[package]\nname = "a"\n',
            "elsewhere/b/Cargo.toml": '[package]\nname = "b"\n',
        }
        self.versions(dict(manifests, **{
            "src/change.rs": "fn old() {}\n",
            "src/sibling.rs": "fn example() {}\n",
            "far/context.txt": "Explicit project mechanism.\n",
        }), {"src/change.rs": "fn new() {}\n"})
        contexts, gaps, _, selection = self.capture(
            [self.record(), self.record("Cargo.toml", "context")],
            ["far/context.txt", "src/change.rs", "far/context.txt"], max_files=5,
        )
        paths = [item["path"] for item in contexts]
        self.assertEqual(paths[:2], ["src/change.rs", "far/context.txt"])
        self.assertEqual(set(paths[2:]), set(manifests))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(any("File limit" in gap and "src/sibling.rs" in gap for gap in gaps))
        self.assertEqual(selection["requested_paths"], ["far/context.txt", "src/change.rs"])
        for item in contexts[2:]:
            self.assertEqual(item["status"], "context")
            self.assertEqual(item["changed_lines"], [])
            self.assertIn("required HEAD workspace Cargo manifest", item["selection_reasons"])

    def test_file_cap_does_not_read_omitted_blob_payloads(self):
        self.versions({
            "src/change.rs": "fn old() {}\n",
            "Cargo.toml": '[package]\nname = "fixture"\n',
            "src/large.rs": "// large sibling\n" * 200,
            "far/context.rs": "fn explicit_example() {}\n",
        }, {"src/change.rs": "fn new() {}\n"})
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, gaps, _, selection = self.capture(requested=["far/context.rs"], max_files=1)
        self.assertEqual([item["path"] for item in contexts], ["src/change.rs"])
        self.assertEqual(set(self.read_oids(spy)),
                         {self.oid(self.base, "src/change.rs"), self.oid(self.head, "src/change.rs")})
        for name in ("Cargo.toml", "src/large.rs", "far/context.rs"):
            self.assertTrue(any("File limit" in gap and name in gap for gap in gaps), name)
        self.assertEqual(selection["omitted_count"], 3)
        self.assertEqual(selection["omission_counts"], {"file_limit": 3})
        for call in spy.call_args_list:
            if "diff" in call.args:
                self.assertIn("--no-ext-diff", call.args)
                self.assertIn("--no-textconv", call.args)
                self.assertIn("--literal-pathspecs", call.args)
                self.assertNotIn(self.base, call.args)
                self.assertNotIn(self.head, call.args)

    def test_byte_cap_does_not_read_omitted_blob_payloads(self):
        old, new = "fn old() {}\n", "fn new() {}\n"
        self.versions({
            "src/change.rs": old,
            "Cargo.toml": '[package]\nname = "fixture"\n',
            "src/large.rs": "// many bytes\n" * 100,
        }, {"src/change.rs": new})
        budget = len(old.encode()) + len(new.encode())
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, gaps, consumed, selection = self.capture(max_bytes=budget)
        self.assertEqual(consumed, budget)
        self.assertEqual(self.read_oids(spy),
                         [self.oid(self.base, "src/change.rs"), self.oid(self.head, "src/change.rs")])
        self.assertEqual(contexts[0]["before"], old)
        self.assertEqual(contexts[0]["after"], new)
        for item in contexts[1:]:
            self.assertIsNone(item["before"])
            self.assertIsNone(item["after"])
        for name in ("Cargo.toml", "src/large.rs"):
            self.assertTrue(any("Byte limit" in gap and name in gap for gap in gaps), name)
        self.assertEqual(selection["omission_counts"], {"byte_limit": 2})

    def test_missing_budgeted_before_never_falls_back_to_head_as_baseline(self):
        self.versions({"src/change.rs": "// old oversized source\n" * 100},
                      {"src/change.rs": "fn new() {}\n"})
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, gaps, consumed, selection = self.capture(max_bytes=12)
        self.assertIsNone(contexts[0]["before"])
        self.assertEqual(contexts[0]["after"], "fn new() {}\n")
        self.assertEqual(contexts[0]["changed_lines"], [])
        self.assertEqual(consumed, len("fn new() {}\n"))
        self.assertEqual(self.read_oids(spy), [self.oid(self.head, "src/change.rs")])
        self.assertFalse(any("diff" in call.args for call in spy.call_args_list))
        self.assertTrue(any("Byte limit" in gap and "before" in gap for gap in gaps))
        self.assertEqual(selection["selected"][0]["captured_versions"], ["HEAD"])

    def test_duplicate_paths_budget_both_versions_once_per_record(self):
        text = "fn sample() {}\n"
        self.versions({"far/sample.rs": text, "src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        contexts, gaps, consumed, selection = self.capture(
            [], ["far/sample.rs", "far/sample.rs"], max_bytes=2 * len(text), max_files=1,
        )
        self.assertEqual(len(contexts), 1)
        self.assertEqual(gaps, [])
        self.assertEqual(consumed, 2 * len(text))
        self.assertEqual(selection["requested_paths"], ["far/sample.rs"])
        contexts, gaps, consumed, selection = self.capture(
            [], ["far/sample.rs"], max_bytes=len(text), max_files=1,
        )
        self.assertEqual(contexts[0]["before"], text)
        self.assertIsNone(contexts[0]["after"])
        self.assertEqual(consumed, len(text))
        self.assertTrue(any("Byte limit" in gap and "after" in gap for gap in gaps))
        self.assertEqual(selection["omitted_count"], 1)

    def test_zero_caps_never_read_nonempty_sources(self):
        self.versions({"src/change.rs": "old\n", "Cargo.toml": "[workspace]\n"},
                      {"src/change.rs": "new\n"})
        for files, size, expected in ((0, 100, "File limit"), (5, 0, "Byte limit")):
            with self.subTest(max_files=files, max_bytes=size):
                with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
                    _, gaps, consumed, selection = self.capture(max_files=files, max_bytes=size)
                self.assertEqual(self.read_oids(spy), [])
                self.assertEqual(consumed, 0)
                self.assertTrue(any(expected in gap for gap in gaps))
                self.assertEqual(selection["omitted_count"], 2)

    def test_unsafe_requested_path_or_type_is_rejected_before_git(self):
        bad_paths = [
            "", ".", "..", "../secret", "a/../secret", "a/./file", "a//file", "a/",
            "/absolute", "C:/absolute", "C:\\absolute", "\\\\server\\share", "a\\b",
            "a:stream", "a\nb", "a\0b", "a\x7fb", "a\u0085b", "a\u202eb", "a\u2028b",
            "a/*.rs", "a/?.rs", "a/[ab].rs", None, 3, {"path": "a"},
        ]
        with patch.object(repository_context, "git") as spy:
            for value in bad_paths:
                with self.subTest(path=repr(value)), self.assertRaises(Error):
                    capture_context(self.root, "HEAD", "HEAD", [], 5, 100, {}, [value])
            for value in ("src/file.rs", {"path": "src/file.rs"}, ("src/file.rs",), 1, True):
                with self.subTest(type=repr(value)), self.assertRaises(Error):
                    capture_context(self.root, "HEAD", "HEAD", [], 5, 100, {}, value)
            spy.assert_not_called()

    def test_invalid_limits_config_and_changed_paths_are_rejected(self):
        with patch.object(repository_context, "git") as spy:
            for files, size in ((-1, 1), (1, -1), (True, 1), (1, 1.5)):
                with self.subTest(limits=(files, size)), self.assertRaises(Error):
                    capture_context(self.root, "HEAD", "HEAD", [], files, size, {})
            for config in ([], {"exclusions": "**"}, {"generated_paths": [3]}):
                with self.subTest(config=config), self.assertRaises(Error):
                    capture_context(self.root, "HEAD", "HEAD", [], 1, 100, config)
            for record in (self.record("../bad.rs"), self.record(old_path="a/../bad.rs"),
                           {"path": "src/a.rs", "status": "M"}):
                with self.subTest(record=record), self.assertRaises(Error):
                    capture_context(self.root, "HEAD", "HEAD", [record], 1, 100, {})
            spy.assert_not_called()

    def test_missing_explicit_file_is_a_visible_unavailable_request(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        contexts, gaps, _, selection = self.capture(requested=["far/missing.rs"])
        self.assertTrue(any("Unavailable" in gap and "far/missing.rs" in gap for gap in gaps))
        missing = next(item for item in contexts if item["path"] == "far/missing.rs")
        self.assertIsNone(missing["before"])
        self.assertIsNone(missing["after"])
        self.assertEqual(missing["status"], "context")
        self.assertEqual(missing["changed_lines"], [])
        selected = next(item for item in selection["selected"] if item["path"] == "far/missing.rs")
        self.assertEqual(selected["captured_versions"], [])
        self.assertEqual(selection["omissions"][0]["path"], "far/missing.rs")

    def test_missing_wanted_changed_versions_are_not_expected_absences(self):
        self.versions({"src/removed.rs": "fn removed() {}\n"},
                      {"src/removed.rs": None, "src/added.rs": "fn added() {}\n"})
        contexts, gaps, _, selection = self.capture(
            [self.record("src/added.rs", "M"), self.record("src/removed.rs", "M")],
        )
        self.assertTrue(any("Unavailable before" in gap and "src/added.rs" in gap for gap in gaps))
        self.assertTrue(any("Unavailable after" in gap and "src/removed.rs" in gap for gap in gaps))
        self.assertTrue(all(item["changed_lines"] == [] for item in contexts))
        self.assertEqual(selection["omitted_count"], 2)

    def test_binary_and_non_utf8_sources_remain_explicit_gaps(self):
        self.versions({
            "src/change.rs": "fn old() {}\n",
            "src/binary.rs": b"a\0b\n",
            "src/invalid.rs": b"\xff\xfe\n",
        }, {"src/change.rs": "fn new() {}\n"})
        contexts, gaps, consumed, selection = self.capture()
        by_path = {item["path"]: item for item in contexts}
        for name in ("src/binary.rs", "src/invalid.rs"):
            self.assertIsNone(by_path[name]["before"])
            self.assertIsNone(by_path[name]["after"])
        self.assertTrue(any("Binary" in gap and "src/binary.rs" in gap for gap in gaps))
        self.assertTrue(any("Unavailable" in gap and "src/invalid.rs" in gap for gap in gaps))
        self.assertEqual(consumed, 2 * len("fn old() {}\n") + 2 * (len(b"a\0b\n") + len(b"\xff\xfe\n")))
        self.assertEqual(selection["omission_counts"], {"binary": 1, "unavailable": 1})

    def test_git_blob_errors_do_not_use_dirty_worktree_fallback(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        wanted = self.oid(self.base, "src/change.rs")
        real_git = repository_context.git

        def fail_blob(root, *args, **kwargs):
            if "cat-file" in args and wanted in args:
                raise Error("Synthetic missing object")
            return real_git(root, *args, **kwargs)

        self.write({"src/change.rs": "mutable fallback is forbidden\n"})
        with patch.object(repository_context, "git", side_effect=fail_blob):
            contexts, gaps, _, selection = self.capture()
        self.assertIsNone(contexts[0]["before"])
        self.assertEqual(contexts[0]["after"], "new\n")
        self.assertEqual(contexts[0]["changed_lines"], [])
        self.assertTrue(any("Synthetic missing object" in gap for gap in gaps))
        self.assertEqual(selection["omitted_count"], 1)

    def test_git_tree_error_is_visible_and_does_not_hide_manifest_coverage_loss(self):
        self.versions({"src/change.rs": "old\n", "Cargo.toml": "[workspace]\n"},
                      {"src/change.rs": "new\n"})
        real_git = repository_context.git

        def fail_tree(root, *args, **kwargs):
            if "ls-tree" in args and self.head in args:
                raise Error("Synthetic tree read failure")
            return real_git(root, *args, **kwargs)

        with patch.object(repository_context, "git", side_effect=fail_tree):
            contexts, gaps, _, selection = self.capture()
        self.assertTrue(any("HEAD tree context unavailable" in gap for gap in gaps))
        self.assertFalse(selection["search_scope"]["head_tree_available"])
        self.assertTrue(all(item["after"] is None for item in contexts))
        self.assertTrue(any("Unavailable after" in gap and "Cargo.toml" in gap for gap in gaps))

    def test_inconsistent_blob_bytes_or_size_are_never_captured_as_evidence(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        wanted = self.oid(self.base, "src/change.rs")
        real_git = repository_context.git
        for raw, diagnostic in ((b"x\n", "Blob length"), (b"bad\n", "immutable object ID")):
            with self.subTest(raw=raw):
                def corrupt_blob(root, *args, **kwargs):
                    if "cat-file" in args and wanted in args:
                        return raw
                    return real_git(root, *args, **kwargs)

                with patch.object(repository_context, "git", side_effect=corrupt_blob):
                    contexts, gaps, _, _ = self.capture()
                self.assertIsNone(contexts[0]["before"])
                self.assertIsNone(contexts[0]["before_blob"])
                self.assertEqual(contexts[0]["changed_lines"], [])
                self.assertTrue(any(diagnostic in gap for gap in gaps))

    def test_duplicate_tree_paths_are_rejected_instead_of_selecting_a_version(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        real_git = repository_context.git

        def duplicate_tree(root, *args, **kwargs):
            output = real_git(root, *args, **kwargs)
            return output + output if "ls-tree" in args else output

        with patch.object(repository_context, "git", side_effect=duplicate_tree) as spy:
            contexts, gaps, _, selection = self.capture()
        self.assertIsNone(contexts[0]["before"])
        self.assertIsNone(contexts[0]["after"])
        self.assertEqual(self.read_oids(spy), [])
        self.assertEqual(selection["search_scope"]["rejected_tree_entries"], 2)
        self.assertTrue(any("Duplicate Git tree path" in gap for gap in gaps))

    def test_unsafe_undecodable_and_malformed_tree_entries_are_never_source(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        real_git = repository_context.git
        oid = self.oid(self.base, "src/change.rs").encode("ascii")
        bad_rows = [b"100644 blob " + oid + b" 4\t" + name + b"\0"
                    for name in (b"../bad.rs", b"src/\xff.rs", b"src/control\x7f.rs")]
        bad_rows.append(b"malformed-tree-entry\0")

        def corrupt_tree(root, *args, **kwargs):
            output = real_git(root, *args, **kwargs)
            return output + b"".join(bad_rows) if "ls-tree" in args else output

        with patch.object(repository_context, "git", side_effect=corrupt_tree):
            contexts, gaps, _, selection = self.capture()
        self.assertEqual([item["path"] for item in contexts], ["src/change.rs"])
        self.assertEqual(selection["search_scope"]["rejected_tree_entries"], 8)
        self.assertEqual(sum("tree entry not inspected" in gap for gap in gaps), 8)

    def test_symlink_and_gitlink_modes_are_not_read_but_executable_regular_files_are(self):
        self.write({"src/change.rs": "old\n"})
        self.base = self.commit()
        self.write({"src/change.rs": "new\n"})
        git_command(self.root, "add", "--", "src/change.rs")
        payload = self.directory / "synthetic-blob"
        payload.write_bytes(b"../not-source\n")
        symlink_oid = git_command(self.root, "hash-object", "-w", "--", str(payload))
        payload.write_bytes(b"fn executable_source() {}\n")
        executable_oid = git_command(self.root, "hash-object", "-w", "--", str(payload))
        for mode, oid, name in (
            ("120000", symlink_oid, "src/link.rs"),
            ("160000", self.base, "vendor/submodule"),
            ("100755", executable_oid, "src/executable.rs"),
        ):
            git_command(self.root, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{name}")
        git_command(self.root, "commit", "--quiet", "-m", "Synthetic mode fixtures")
        self.head = git_command(self.root, "rev-parse", "HEAD")
        snapshot = file_tree(self.root)
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, gaps, _, selection = self.capture(
                requested=["src/link.rs", "vendor/submodule", "src/executable.rs"],
            )
        self.assertEqual(snapshot, file_tree(self.root))
        self.assertNotIn(symlink_oid, self.read_oids(spy))
        self.assertNotIn(self.base, self.read_oids(spy))
        by_path = {item["path"]: item for item in contexts}
        for name, mode in (("src/link.rs", "120000"), ("vendor/submodule", "160000")):
            self.assertIsNone(by_path[name]["before"])
            self.assertIsNone(by_path[name]["after"])
            self.assertTrue(any("Non-regular" in gap and name in gap and mode in gap for gap in gaps))
        self.assertEqual(by_path["src/executable.rs"]["after"], "fn executable_source() {}\n")
        self.assertEqual(selection["omission_counts"], {"non_regular": 2})

    def test_changed_paths_are_literal_and_diff_reads_only_captured_blob_ids(self):
        name = "src/source[1].rs"
        self.versions({
            name: "first\nsecond\u2028literal\n",
            "src/source1.rs": "// not selected\n" * 200,
        }, {name: "first\nsecond\u2028changed\n"})
        with patch.object(repository_context, "git", wraps=repository_context.git) as spy:
            contexts, _, _, _ = self.capture([self.record(name)], max_files=1)
        self.assertEqual(contexts[0]["changed_lines"], [2])
        self.assertEqual(contexts[0]["before"], "first\nsecond\u2028literal\n")
        self.assertEqual(contexts[0]["after"], "first\nsecond\u2028changed\n")
        self.assertEqual(set(self.read_oids(spy)), {self.oid(self.base, name), self.oid(self.head, name)})
        diff = next(call.args for call in spy.call_args_list if "diff" in call.args)
        self.assertIn("--literal-pathspecs", diff)
        self.assertIn("--no-ext-diff", diff)
        self.assertIn("--no-textconv", diff)
        self.assertEqual(diff[-3:], (self.oid(self.base, name), self.oid(self.head, name), "--"))

    def test_static_rust_links_use_captured_versions_not_comments_strings_or_macros(self):
        source = (
            '#[path = "custom/transport.rs"]\nmod transport;\n'
            "mod old;\nuse crate::services::Client;\nuse super::types::Payload;\n"
            "/* outer /* nested */\nmod commented;\n*/\n"
            'const TEXT: &str = r##"\nmod quoted;\n"##;\n'
            'const OTHER: &str = "\\\"\\nmod string_literal;";\n'
            "wrapper! {\nmod macro_only;\n}\n"
            "fn body() {\nmod function_local;\n}\n"
        )
        before = {
            "src/feature.rs": source,
            "src/lib.rs": "pub mod feature;\npub mod services;\npub mod types;\n",
            "src/custom/transport.rs": "pub struct Transport;\n",
            "src/feature/old.rs": "pub fn precedent() {}\n",
            "src/services.rs": "pub struct Client;\n",
            "src/types.rs": "pub struct Payload;\n",
        }
        for name in ("commented", "quoted", "string_literal", "macro_only", "function_local"):
            before[f"src/feature/{name}.rs"] = f"fn {name}() {{}}\n"
        self.versions(before, {
            "src/feature.rs": source.replace("mod old;", "mod new;"),
            "src/feature/old.rs": None,
            "src/feature/new.rs": "pub fn new_example() {}\n",
        })
        contexts, gaps, _, _ = self.capture([self.record("src/feature.rs")])
        self.assertEqual(gaps, [])
        by_path = {item["path"]: item for item in contexts}
        for name in ("src/custom/transport.rs", "src/services.rs", "src/types.rs",
                     "src/feature/old.rs", "src/feature/new.rs"):
            self.assertTrue(any("static Rust relationship" in r for r in by_path[name]["selection_reasons"]), name)
        for name in ("commented", "quoted", "string_literal", "macro_only", "function_local"):
            self.assertFalse(any("static Rust relationship" in r
                                 for r in by_path[f"src/feature/{name}.rs"]["selection_reasons"]), name)
        self.assertEqual(by_path["src/feature/old.rs"]["before"], "pub fn precedent() {}\n")
        self.assertIsNone(by_path["src/feature/old.rs"]["after"])
        self.assertIsNone(by_path["src/feature/new.rs"]["before"])
        self.assertEqual(by_path["src/feature/new.rs"]["after"], "pub fn new_example() {}\n")

    def test_automatic_context_prefers_base_precedents_over_head_only_siblings(self):
        self.versions({
            "src/change.rs": "fn old() {}\n",
            "src/z_precedent.rs": "fn established() {}\n",
        }, {
            "src/change.rs": "fn new() {}\n",
            "src/a_head_only.rs": "fn not_a_preexisting_convention() {}\n",
        })
        contexts, gaps, _, selection = self.capture(max_files=2)
        self.assertEqual([item["path"] for item in contexts], ["src/change.rs", "src/z_precedent.rs"])
        self.assertTrue(any("File limit" in gap and "src/a_head_only.rs" in gap for gap in gaps))
        self.assertEqual(selection["omitted_count"], 1)

    def test_selection_is_deterministic_when_git_tree_enumeration_order_changes(self):
        self.versions({
            "src/change.rs": "use crate::shared::Value;\nfn old() {}\n",
            "src/lib.rs": "pub mod shared;\n",
            "src/shared.rs": "pub struct Value;\n",
            "src/a.rs": "fn a() {}\n",
            "src/z.rs": "fn z() {}\n",
            "Cargo.toml": "[workspace]\n",
            "README.md": "# Example\n",
        }, {"src/change.rs": "use crate::shared::Value;\nfn new() {}\n"})
        expected = self.capture(max_files=5)
        real_git = repository_context.git

        def reverse_tree(root, *args, **kwargs):
            output = real_git(root, *args, **kwargs)
            if "ls-tree" in args:
                return b"\0".join(reversed(output.rstrip(b"\0").split(b"\0"))) + b"\0"
            return output

        with patch.object(repository_context, "git", side_effect=reverse_tree):
            actual = self.capture(max_files=5)
        self.assertEqual(actual, expected)

    def test_replace_refs_cannot_substitute_repository_evidence(self):
        self.versions({"src/change.rs": "old\n"}, {"src/change.rs": "new\n"})
        actual_oid = self.oid(self.head, "src/change.rs")
        replacement = self.directory / "replacement-blob"
        replacement.write_bytes(b"replaced mutable evidence\n")
        replacement_oid = git_command(self.root, "hash-object", "-w", "--", str(replacement))
        git_command(self.root, "replace", actual_oid, replacement_oid)
        snapshot = file_tree(self.root)
        contexts, gaps, _, _ = self.capture()
        self.assertEqual(gaps, [])
        self.assertEqual(contexts[0]["after"], "new\n")
        self.assertEqual(contexts[0]["after_blob"], actual_oid)
        self.assertEqual(snapshot, file_tree(self.root))

    def test_lf_only_line_helpers_preserve_unicode_separators_and_crlf(self):
        self.assertEqual(_source_lines("a\u2028b\u2029c\u0085d\r\nnext\r\n"),
                         ["a\u2028b\u2029c\u0085d", "next"])
        self.assertEqual(_source_lines(""), [])
        self.assertEqual(_source_lines("\n"), [""])
        self.assertEqual(_source_lines("last\r"), ["last"])
        self.assertEqual(_added_lines(
            "diff --git a/file b/file\n--- a/file\n+++ b/file\n"
            "@@ -1,1 +1,2 @@\n-old\n+new\u2028literal\n+next\n"
            "@@ -5 +6 @@\n-old\n+new\n"
        ), [1, 2, 6])
        self.assertEqual(_path("source/文 件.rs"), "source/文 件.rs")


if __name__ == "__main__":
    unittest.main()
