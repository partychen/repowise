import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / ".github" / "skills" / "repowise" / "scripts"))

from repowise.review_contract import REPOSITORY_DIMENSIONS

spec = importlib.util.spec_from_file_location("package_skill", PROJECT / "tools" / "package_skill.py")
package_skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_skill)


class PortabilityTests(unittest.TestCase):
    def test_archive_excludes_local_data_and_has_no_location_dependency(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as directory:
            temporary = Path(directory)
            source = temporary / "arbitrary source name"
            skill = source / ".github" / "skills" / "repowise"
            shutil.copytree(PROJECT / ".github" / "skills" / "repowise", skill,
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
                self.assertIn("repowise/SKILL.md", names)
                self.assertIn("repowise/requirements.txt", names)
                self.assertEqual(archive.read("repowise/requirements.txt"),
                                 (moved / ".github" / "skills" / "repowise" / "requirements.txt").read_bytes())
                self.assertIn("repowise/references/setup.md", names)
                self.assertIn("repowise/scripts/repowise/storage.py", names)
                self.assertIn("repowise/scripts/repowise/approvals.py", names)
                self.assertIn("repowise/scripts/repowise/repository_context.py", names)
                self.assertIn("repowise/scripts/repowise/review_contract.py", names)
                self.assertIn("repowise/scripts/repowise/review_agents.py", names)
                self.assertIn("repowise/scripts/repowise/feature.py", names)
                self.assertIn("repowise/scripts/repowise/pull_requests.py", names)
                self.assertIn("repowise/references/feature.md", names)
                self.assertIn("repowise/references/review-agents.md", names)
                self.assertIn("repowise/packs/sources/actionbook-rust-skills.json", names)
                self.assertIn("repowise/wheels/LICENSE.PyYAML.txt", names)
                provenance = json.loads(archive.read("repowise/wheels/provenance.json"))
                wheel_name = "repowise/wheels/" + provenance["wheel"]["filename"]
                self.assertIn(wheel_name, names)
                self.assertEqual(archive.read(wheel_name),
                                 (moved / ".github" / "skills" / "repowise" /
                                  "wheels" / provenance["wheel"]["filename"]).read_bytes())
                self.assertFalse(any(".review/" in name or "__pycache__" in name or name.endswith(".log") for name in names))
                self.assertTrue(all(name.startswith("repowise/") and "\\" not in name for name in names))
                installed_skill = moved / ".github" / "skills" / "repowise"
                documents = [installed_skill / "SKILL.md", *(installed_skill / "references").glob("*.md")]
                for document in documents:
                    for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                        if "://" in link or link.startswith("#"):
                            continue
                        linked = (document.parent / link.split("#", 1)[0]).resolve()
                        self.assertTrue(linked.is_relative_to(installed_skill.resolve()), link)
                        member = "repowise/" + linked.relative_to(installed_skill.resolve()).as_posix()
                        self.assertIn(member, names, f"Unpackaged workflow link in {document.name}: {link}")
            (moved / ".github" / "skills" / "repowise" / "SKILL.md").write_text("Changed release")
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                package_skill.build_archive(moved, temporary / "second")

    def test_archive_rejects_missing_corrupt_or_unpinned_dependency(self):
        with tempfile.TemporaryDirectory(dir=PROJECT) as directory:
            temporary = Path(directory)
            source = temporary / "source"
            skill = source / ".github" / "skills" / "repowise"
            shutil.copytree(PROJECT / ".github" / "skills" / "repowise", skill,
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
            (target / "repowise.py").write_text('raise RuntimeError("Untrusted target module executed")')
            installed = target / "installed skill"
            shutil.copytree(PROJECT / ".github" / "skills" / "repowise", installed,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            repository = target / "project"
            repository.mkdir()
            (repository / "repowise.py").write_text('raise RuntimeError("Untrusted target module executed")')
            env = dict(os.environ)
            env["PYTHONPATH"] = str(target)
            env["REPOWISE_CACHE"] = str(target / "cache")
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
        files = [PROJECT / "README.md", *list((PROJECT / ".github" / "skills" / "repowise").rglob("*.md"))]
        for path in files:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("D:\\Tools", content)
                self.assertNotIn("python $Tool", content)

    def test_review_reference_and_example_follow_shared_dimensions(self):
        reference = (PROJECT / ".github" / "skills" / "repowise" / "references" /
                     "review.md").read_text(encoding="utf-8")
        example = json.loads((PROJECT / "examples" / "model-response.json").read_text(encoding="utf-8"))
        self.assertEqual(set(REPOSITORY_DIMENSIONS),
                         {item["dimension"] for item in example["repository_assessments"]})
        for dimension in REPOSITORY_DIMENSIONS:
            self.assertIn(f"| `{dimension}` |", reference)


if __name__ == "__main__":
    unittest.main()
