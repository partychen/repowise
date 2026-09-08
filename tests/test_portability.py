import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", PROJECT / "tools" / "package_skill.py")
package_skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_skill)


class PortabilityTests(unittest.TestCase):
    def test_archive_excludes_local_data_and_has_no_location_dependency(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as directory:
            temporary = Path(directory)
            source = temporary / "arbitrary source name"
            skill = source / ".github" / "skills" / "review-memory"
            shutil.copytree(PROJECT / ".github" / "skills" / "review-memory", skill,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy2(PROJECT / "pyproject.toml", source / "pyproject.toml")
            (skill / ".review" / "local").mkdir(parents=True)
            (skill / ".review" / "local" / "secret.json").write_text('{"private":true}')
            (skill / "scripts" / "unexpected.log").write_text("local diagnostic")
            first = package_skill.build_archive(source, temporary / "first")
            moved = temporary / "moved installation"
            source.rename(moved)
            second = package_skill.build_archive(moved, temporary / "second")
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(second, package_skill.build_archive(moved, temporary / "second"))
            with ZipFile(first["archive"]) as archive:
                names = archive.namelist()
                self.assertIn("review-memory/SKILL.md", names)
                self.assertIn("review-memory/requirements.txt", names)
                self.assertEqual(archive.read("review-memory/requirements.txt"),
                                 (moved / ".github" / "skills" / "review-memory" / "requirements.txt").read_bytes())
                self.assertIn("review-memory/references/setup.md", names)
                self.assertIn("review-memory/scripts/review_memory/storage.py", names)
                self.assertIn("review-memory/scripts/review_memory/approvals.py", names)
                self.assertIn("review-memory/scripts/review_memory/repository_context.py", names)
                self.assertIn("review-memory/scripts/review_memory/review_contract.py", names)
                self.assertIn("review-memory/scripts/review_memory/feature.py", names)
                self.assertIn("review-memory/scripts/review_memory/pull_requests.py", names)
                self.assertIn("review-memory/references/feature.md", names)
                self.assertIn("review-memory/packs/sources/actionbook-rust-skills.json", names)
                self.assertIn("review-memory/wheels/LICENSE.PyYAML.txt", names)
                provenance = json.loads(archive.read("review-memory/wheels/provenance.json"))
                wheel_name = "review-memory/wheels/" + provenance["wheel"]["filename"]
                self.assertIn(wheel_name, names)
                self.assertEqual(archive.read(wheel_name),
                                 (moved / ".github" / "skills" / "review-memory" /
                                  "wheels" / provenance["wheel"]["filename"]).read_bytes())
                self.assertFalse(any(".review/" in name or "__pycache__" in name or name.endswith(".log") for name in names))
                self.assertTrue(all(name.startswith("review-memory/") and "\\" not in name for name in names))
            (moved / ".github" / "skills" / "review-memory" / "SKILL.md").write_text("Changed release")
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                package_skill.build_archive(moved, temporary / "second")

    def test_archive_rejects_missing_corrupt_or_unpinned_dependency(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as directory:
            temporary = Path(directory)
            source = temporary / "source"
            skill = source / ".github" / "skills" / "review-memory"
            shutil.copytree(PROJECT / ".github" / "skills" / "review-memory", skill,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copy2(PROJECT / "pyproject.toml", source / "pyproject.toml")
            provenance = json.loads((skill / "wheels" / "provenance.json").read_text(encoding="utf-8"))
            wheel = skill / "wheels" / provenance["wheel"]["filename"]
            original = wheel.read_bytes()
            wheel.write_bytes(original + b"tampered")
            with self.assertRaisesRegex(ValueError, "dependency hash"):
                package_skill.build_archive(source, temporary / "output")
            wheel.write_bytes(original)
            (skill / "requirements.txt").write_text(f"PyYAML=={provenance['version']}\n")
            with self.assertRaisesRegex(ValueError, "differs from requirements"):
                package_skill.build_archive(source, temporary / "output")
            wheel.unlink()
            with self.assertRaises(FileNotFoundError):
                package_skill.build_archive(source, temporary / "output")
            self.assertFalse((temporary / "output").exists())

    def test_missing_runtime_does_not_execute_shadowing_target_file(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as directory:
            target = Path(directory)
            (target / "review_memory.py").write_text('raise RuntimeError("Untrusted target module executed")')
            installed = target / "installed skill"
            shutil.copytree(PROJECT / ".github" / "skills" / "review-memory", installed,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            repository = target / "project"
            repository.mkdir()
            (repository / "review_memory.py").write_text('raise RuntimeError("Untrusted target module executed")')
            env = dict(os.environ)
            env["PYTHONPATH"] = str(target)
            env["REVIEW_MEMORY_CACHE"] = str(target / "cache")
            result = subprocess.run(
                [sys.executable, "-I", str(installed / "scripts" / "main.py"), "doctor"],
                cwd=repository, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Runtime setup is required", result.stderr)
            self.assertIn(str(installed / "scripts" / "main.py"), result.stderr)
            self.assertNotIn("Untrusted target module executed", result.stderr)
            self.assertFalse((target / "cache").exists())

    def test_user_docs_have_no_machine_specific_tool_location(self):
        files = [PROJECT / "README.md", *list((PROJECT / ".github" / "skills" / "review-memory").rglob("*.md"))]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("D:\\Tools", content)
                self.assertNotIn("python $Tool", content)


if __name__ == "__main__":
    unittest.main()
