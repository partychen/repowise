import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "skills" /
                       "review-memory" / "scripts"))

from review_memory.detectors import TOOL_ID, dependency_edges, forbidden_dependency


DETECTOR = {"id": "D-deps-001", "revision": 1, "tool_id": TOOL_ID,
            "parameters": {"from_package": "boundary", "to_package": "forbidden"}}
PACKAGE = '[package]\nname = "boundary"\nversion = "0.1.0"\n'


class DetectorTests(unittest.TestCase):
    def test_toml_aliases_comments_strings_and_all_dependency_sections(self):
        text = PACKAGE + '''
description = "forbidden = 1"
# forbidden = "1"
[dependencies]
allowed = "1"
renamed = { package = "forbidden", version = "1", optional = true }
[dev-dependencies.forbidden]
version = "2"
[build-dependencies]
build_alias = { package = "forbidden", path = "../forbidden" }
[target.'cfg(unix)'.dependencies]
target_alias = { package = "forbidden", version = "1" }
'''
        parsed = dependency_edges(text)
        self.assertFalse(parsed["gaps"])
        edges = [e for e in parsed["edges"] if e["package"] == "forbidden"]
        self.assertEqual(4, len(edges))
        self.assertEqual({"dependencies", "dev-dependencies", "build-dependencies",
                          "target.cfg(unix).dependencies"}, {e["section"] for e in edges})

    def test_comments_and_strings_are_not_edges(self):
        parsed = dependency_edges(PACKAGE + 'description = "forbidden"\n# forbidden = "1"\n')
        self.assertEqual([], parsed["edges"])

    def test_workspace_alias_requires_resolution(self):
        text = PACKAGE + '[dependencies]\nalias.workspace = true\n'
        unresolved = dependency_edges(text)
        self.assertFalse(unresolved["edges"])
        self.assertIn("unresolved", unresolved["gaps"][0])
        workspace = '[workspace.dependencies]\nalias = { package = "forbidden", version = "1" }\n'
        resolved = dependency_edges(text, workspace)
        self.assertEqual("forbidden", resolved["edges"][0]["package"])
        self.assertFalse(resolved["gaps"])

    def test_explicit_workspace_is_not_guessed(self):
        text = PACKAGE + 'workspace = "../elsewhere"\n[dependencies]\nalias.workspace = true\n'
        parsed = dependency_edges(text, '[workspace.dependencies]\nalias = "1"\n')
        self.assertFalse(parsed["edges"])
        self.assertTrue(parsed["gaps"])

    def test_new_and_preexisting_edges(self):
        edge = '[dependencies]\nalias = { package = "forbidden", version = "1" }\n'
        contexts = [{"path": "Cargo.toml", "status": "M", "before": PACKAGE, "after": PACKAGE + edge}]
        result = forbidden_dependency(DETECTOR, contexts)
        self.assertEqual("introduced", result["findings"][0]["novelty"])
        self.assertEqual("summary", result["findings"][0]["placement"])
        self.assertIsNone(result["findings"][0]["evidence"]["line_start"])
        contexts[0]["before"] = contexts[0]["after"]
        result = forbidden_dependency(DETECTOR, contexts)
        self.assertEqual("pre_existing", result["findings"][0]["novelty"])

    def test_missing_baseline_not_labeled_new(self):
        result = forbidden_dependency(DETECTOR, [{
            "path": "Cargo.toml", "status": "M", "before": None,
            "after": PACKAGE + '[dependencies]\nforbidden = "1"\n'}])
        self.assertEqual("unknown", result["findings"][0]["novelty"])
        self.assertTrue(result["coverage_gaps"])

    def test_moving_dependency_section_preserves_existing_package_edge(self):
        result = forbidden_dependency(DETECTOR, [{
            "path": "Cargo.toml", "status": "M",
            "before": PACKAGE + '[dev-dependencies]\nforbidden = "1"\n',
            "after": PACKAGE + '[dependencies]\nforbidden = "1"\n'}])
        self.assertEqual("pre_existing", result["findings"][0]["novelty"])

    def test_workspace_change_introduces_edge(self):
        contexts = [
            {"path": "Cargo.toml", "status": "M",
             "before": '[workspace.dependencies]\nalias = {package = "allowed", version = "1"}\n',
             "after": '[workspace.dependencies]\nalias = {package = "forbidden", version = "1"}\n'},
            {"path": "crate/Cargo.toml", "status": "context",
             "before": PACKAGE + '[dependencies]\nalias.workspace = true\n',
             "after": PACKAGE + '[dependencies]\nalias.workspace = true\n'}]
        result = forbidden_dependency(DETECTOR, contexts)
        self.assertEqual("introduced", result["findings"][0]["novelty"])
        self.assertEqual("crate/Cargo.toml", result["findings"][0]["evidence"]["path"])

    def test_invalid_manifest_and_unknown_tool_are_gaps(self):
        self.assertTrue(dependency_edges("[package")["gaps"])
        result = forbidden_dependency(dict(DETECTOR, tool_id="shell"), [])
        self.assertTrue(result["coverage_gaps"])
        self.assertFalse(result["findings"])


if __name__ == "__main__":
    unittest.main()
