import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_core import knowledge
from review_memory.common import Error, git, is_link, load_json, write_json
from review_memory.storage import (
    initialize_project, project_config, project_storage, validate_memory_root,
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.target = self.directory / "target"
        self.target.mkdir()
        (self.target / ".gitignore").write_text("keep-existing-ignore\n")
        (self.target / "source.rs").write_text("fn main() {}\n")
        self.home = self.directory / "memory"

    def tree(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def test_default_location_is_persistent_user_data_and_resolution_is_read_only(self):
        home = self.directory / "user"
        with patch.dict(os.environ), patch("pathlib.Path.home", return_value=home):
            os.environ.pop("REVIEW_MEMORY_HOME", None)
            storage = project_storage(self.target)
        self.assertEqual(storage.data_home, home / ".review-memory" / "projects")
        self.assertFalse(storage.storage_root.exists())
        self.assertFalse(home.exists())

    def test_identity_uses_normalized_path_not_only_directory_name(self):
        other = self.directory / "another" / self.target.name
        other.mkdir(parents=True)
        first = project_storage(self.target, self.home)
        second = project_storage(other, self.home)
        self.assertNotEqual(first.project_id, second.project_id)
        self.assertEqual(first, project_storage(self.target / ".." / self.target.name, self.home))
        self.assertEqual(first.project_id, project_storage(self.target, self.directory / "other-home").project_id)

    def test_initialization_binds_repository_and_never_changes_target(self):
        before = self.tree(self.target)
        storage = project_storage(self.target, self.home)
        result = initialize_project(storage, "Acme/Project")
        self.assertEqual(result["status"], "initialized")
        self.assertEqual(project_config(storage)["repository"], "acme/project")
        self.assertEqual(load_json(storage.storage_root / ".review" / "project.json"),
                         storage.binding("acme/project"))
        self.assertEqual(initialize_project(storage, "acme/project")["status"], "already_initialized")
        self.assertEqual(before, self.tree(self.target))
        self.assertFalse((self.target / ".review").exists())
        self.assertFalse(list(self.home.glob("*.lock")))

    def test_repository_and_target_binding_changes_are_rejected(self):
        storage = project_storage(self.target, self.home)
        initialize_project(storage, "acme/project")
        with self.assertRaisesRegex(Error, "binding differs"):
            initialize_project(storage, "other/project")
        binding_path = storage.storage_root / ".review" / "project.json"
        binding = load_json(binding_path)
        binding["target_root"] = str(self.directory / "different")
        write_json(binding_path, binding)
        with self.assertRaisesRegex(Error, "binding is missing or changed"):
            project_config(storage)

    def test_missing_binding_does_not_adopt_existing_state(self):
        storage = project_storage(self.target, self.home)
        initialize_project(storage, "acme/project")
        (storage.storage_root / ".review" / "project.json").unlink()
        with self.assertRaisesRegex(Error, "binding is missing"):
            project_config(storage)
        with self.assertRaisesRegex(Error, "no project binding"):
            initialize_project(storage, "acme/project")

    def test_interrupted_initialization_can_resume_only_the_same_binding(self):
        storage = project_storage(self.target, self.home)
        with patch("review_memory.core.initialize", side_effect=Error("interrupted")):
            with self.assertRaisesRegex(Error, "interrupted"):
                initialize_project(storage, "acme/project")
        with self.assertRaisesRegex(Error, "binding differs"):
            initialize_project(storage, "other/project")
        self.assertEqual(initialize_project(storage, "acme/project")["status"], "initialized")

    def test_relative_overlapping_and_skill_locations_are_rejected(self):
        project = Path(__file__).resolve().parents[1]
        for home in (Path("relative"), self.target, self.target / "data", project / "data"):
            with self.subTest(home=home), self.assertRaises(Error):
                project_storage(self.target, home)
        for memory in (self.target, self.target / "memory", self.target.parent):
            with self.subTest(memory=memory), self.assertRaisesRegex(Error, "non-overlapping"):
                validate_memory_root(self.target, memory)

    def test_explicit_data_home_overrides_environment(self):
        with patch.dict(os.environ, {"REVIEW_MEMORY_HOME": str(self.directory / "env-memory")}):
            self.assertEqual(project_storage(self.target).data_home, self.directory / "env-memory")
            self.assertEqual(project_storage(self.target, self.home).data_home, self.home)
        for invalid in ("", "relative"):
            with patch.dict(os.environ, {"REVIEW_MEMORY_HOME": invalid}), self.assertRaises(Error):
                project_storage(self.target)

    def test_namespace_or_review_links_are_rejected(self):
        storage = project_storage(self.target, self.home)
        for linked in (storage.storage_root, storage.storage_root / ".review"):
            with self.subTest(linked=linked), \
                    patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == linked):
                with self.assertRaises(Error):
                    project_storage(self.target, self.home)

    def test_junction_detection_supports_python_311_path_interface(self):
        path = Mock(spec=["is_symlink", "lstat"])
        path.is_symlink.return_value = False
        path.lstat.return_value.st_reparse_tag = 0xA0000003
        self.assertTrue(is_link(path))
        path.lstat.return_value.st_reparse_tag = 0
        self.assertFalse(is_link(path))
        path.lstat.side_effect = FileNotFoundError()
        self.assertFalse(is_link(path))

    def test_git_preserves_empty_environment_config_values(self):
        with patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "reviewmemory.empty",
            "GIT_CONFIG_VALUE_0": "",
        }):
            self.assertEqual(git(self.target, "config", "--get", "reviewmemory.empty"), "")

    def test_initialization_lock_is_not_silently_removed(self):
        storage = project_storage(self.target, self.home)
        self.home.mkdir()
        lock = self.home / f".{storage.project_id}.lock"
        lock.write_text("another process\n")
        with self.assertRaisesRegex(Error, "initialization is locked"):
            initialize_project(storage, "acme/project")
        self.assertTrue(lock.exists())
        self.assertFalse(storage.storage_root.exists())


if __name__ == "__main__":
    unittest.main()
