import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
SKILL = PROJECT / ".github" / "skills" / "review-memory"


def load_launcher(script):
    spec = importlib.util.spec_from_file_location("skill_launcher", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=PROJECT)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.skill = self.directory / "installed skill"
        (self.skill / "scripts").mkdir(parents=True)
        shutil.copy2(SKILL / "requirements.txt", self.skill / "requirements.txt")
        shutil.copytree(SKILL / "wheels", self.skill / "wheels")
        shutil.copy2(SKILL / "scripts" / "main.py", self.skill / "scripts" / "main.py")
        self.launcher = load_launcher(self.skill / "scripts" / "main.py")
        self.cache = self.directory / "cache"
        self.target = self.directory / "target"
        self.target.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"REVIEW_MEMORY_CACHE": str(self.cache)}).start()
        patch.object(self.launcher.Path, "cwd", return_value=self.target).start()
        self.requirement, self.content, self.version = self.launcher.requirements()
        self.key = self.launcher.cache_key(self.content)
        self.environment = self.cache / self.key

    def call(self, *argv):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            result = self.launcher.main(list(argv))
        return result, output.getvalue(), error.getvalue()

    def fake_environment(self, ready=True):
        python = self.launcher.environment_python(self.environment)
        python.parent.mkdir(parents=True)
        python.touch()
        (self.environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        if ready:
            (self.environment / "ready.json").write_text(json.dumps({
                "key": self.key, "environment": str(self.environment),
            }))

    def test_missing_runtime_never_installs_or_imports_global_runtime(self):
        with patch.object(self.launcher.subprocess, "run") as run:
            result, _, error = self.call("doctor")
        self.assertEqual(result, 2)
        self.assertIn("Runtime setup is required", error)
        self.assertIn('python -I "', error)
        run.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_setup_installs_only_pinned_wheel_and_marks_validated_environment(self):
        calls = []

        def invoke(command, **kwargs):
            calls.append((command, kwargs))
            if "venv" in command:
                self.fake_environment(ready=False)
            self.assertFalse((self.environment / "ready.json").exists())
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(self.launcher.subprocess, "run", side_effect=invoke):
            result, output, error = self.call("setup")
        self.assertEqual(result, 0, error)
        self.assertIn("runtime ready", output)
        self.assertIn("Creating isolated virtualenv", error)
        self.assertIn("Installing bundled PyYAML", error)
        self.assertIn("offline", error)
        self.assertIn("Checking cached Python and PyYAML complete", error)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], [sys.executable, "-I", "-m", "venv", str(self.environment)])
        install = calls[1][0]
        self.assertIn("--no-deps", install)
        self.assertIn("--only-binary=:all:", install)
        self.assertIn("--isolated", install)
        self.assertIn("--no-index", install)
        self.assertIn("--no-cache-dir", install)
        self.assertIn("--require-hashes", install)
        self.assertEqual(install[install.index("--find-links") + 1], str(self.skill / "wheels"))
        self.assertFalse(any("https://" in argument or "http://" in argument for argument in install))
        self.assertNotIn("--index-url", install)
        self.assertNotIn("--extra-index-url", install)
        self.assertEqual([kwargs["timeout"] for _, kwargs in calls],
                         [self.launcher.VENV_TIMEOUT, self.launcher.INSTALL_TIMEOUT,
                          self.launcher.PROBE_TIMEOUT])
        self.assertIn(str(self.requirement), install)
        self.assertNotIn("--upgrade", install)
        self.assertNotIn("--system-site-packages", calls[0][0])
        self.assertEqual(calls[1][1]["cwd"], self.environment)
        self.launcher.require_ready(self.environment, self.key)
        self.assertFalse(self.environment.with_suffix(".lock").exists())

    def test_valid_setup_reuses_without_pip(self):
        self.fake_environment()
        with patch.object(self.launcher, "probe_environment") as probe, \
                patch.object(self.launcher.subprocess, "run") as run:
            result, _, error = self.call("setup")
        self.assertEqual(result, 0, error)
        probe.assert_called_once_with(self.environment, self.version)
        run.assert_not_called()

    def test_setup_failure_is_visible_redacted_and_unready(self):
        def invoke(command, **kwargs):
            if "venv" in command:
                self.fake_environment(ready=False)
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(
                command, 1, "", "Could not download from https://user:secret@example.org/package")

        with patch.object(self.launcher.subprocess, "run", side_effect=invoke):
            result, _, error = self.call("setup")
        self.assertEqual(result, 2)
        self.assertIn("Could not download", error)
        self.assertIn("[redacted]@", error)
        self.assertNotIn("secret", error)
        self.assertIn("incomplete/corrupt", error)
        self.assertFalse((self.environment / "ready.json").exists())
        self.assertFalse(self.environment.with_suffix(".lock").exists())

    def test_stale_lock_never_automatically_removed(self):
        self.cache.mkdir()
        lock = self.environment.with_suffix(".lock")
        lock.mkdir()
        with patch.object(self.launcher.subprocess, "run") as run:
            result, _, error = self.call("setup")
        self.assertEqual(result, 2)
        self.assertIn("confirming no setup process is active", error)
        self.assertTrue(lock.exists())
        run.assert_not_called()

    def test_setup_timeout_is_actionable_and_does_not_mark_runtime_ready(self):
        for stage in ("venv", "pip"):
            with self.subTest(stage=stage):
                if self.environment.exists():
                    shutil.rmtree(self.environment)

                def invoke(command, **kwargs):
                    if "venv" in command:
                        self.fake_environment(ready=False)
                    if stage in command:
                        raise subprocess.TimeoutExpired(
                            command, kwargs["timeout"],
                            stderr=b"waiting for https://user:secret@example.org/package")
                    return subprocess.CompletedProcess(command, 0, "", "")

                with patch.object(self.launcher.subprocess, "run", side_effect=invoke):
                    result, _, error = self.call("setup")
                self.assertEqual(result, 2)
                self.assertIn("timed out after 120s", error)
                self.assertIn("incomplete/corrupt", error)
                self.assertIn("[redacted]@", error)
                self.assertNotIn("secret", error)
                self.assertFalse((self.environment / "ready.json").exists())
                self.assertFalse(self.environment.with_suffix(".lock").exists())

    def test_reused_environment_probe_has_short_timeout(self):
        self.fake_environment()
        with patch.object(self.launcher.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("probe", self.launcher.PROBE_TIMEOUT)):
            result, _, error = self.call("setup")
        self.assertEqual(result, 2)
        self.assertIn("Checking cached Python and PyYAML timed out after 30s", error)
        self.assertIn("incomplete/corrupt", error)

    def test_corruption_requires_explicit_recovery_not_fallback(self):
        self.fake_environment()
        for content in ("invalid json", json.dumps({"key": self.key, "environment": "old cache location"})):
            with self.subTest(content=content):
                (self.environment / "ready.json").write_text(content)
                with patch.object(self.launcher.subprocess, "run") as run:
                    result, _, error = self.call("doctor")
                self.assertEqual(result, 2)
                self.assertIn("incomplete/corrupt", error)
                run.assert_not_called()

    def test_system_site_packages_are_rejected(self):
        self.fake_environment()
        (self.environment / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
        result, _, error = self.call("doctor")
        self.assertEqual(result, 2)
        self.assertIn("validation failed", error)

    def test_dangling_environment_link_is_not_followed_during_setup(self):
        with patch.object(self.launcher.Path, "is_symlink", autospec=True,
                          side_effect=lambda path: path == self.environment), \
                patch.object(self.launcher.subprocess, "run") as run:
            result, _, error = self.call("setup")
        self.assertEqual(result, 2)
        self.assertIn("cannot be a symlink or junction", error)
        run.assert_not_called()

    def test_cache_key_depends_on_python_and_requirement_content_not_skill_location(self):
        other = load_launcher(self.skill / "scripts" / "main.py")
        other.SKILL = self.directory / "moved skill"
        self.assertEqual(other.cache_key(self.content), self.key)
        self.assertNotEqual(self.launcher.cache_key(self.content + b"\n"), self.key)
        with patch.object(self.launcher.sys, "version_info", (3, 99, 0)):
            self.assertNotEqual(self.launcher.cache_key(self.content), self.key)

    def test_only_supported_pinned_dependency_is_accepted(self):
        for content in ("PyYAML>=6", "PyYAML==6.0.3\nrequests==1.0.0",
                        "--index-url https://other.invalid\nPyYAML==6.0.3",
                        "PyYAML==6.0.3",
                        "PyYAML==6.0.3 --hash=sha256:invalid",
                        "PyYAML==6.0.1 --hash=sha256:" + "0" * 64,
                        "PyYAML==7.0.0 --hash=sha256:" + "0" * 64,
                        "PyYAML==6.0.3 --hash=sha256:" + "0" * 64 + " --trusted-host example.org"):
            with self.subTest(content=content):
                self.requirement.write_text(content)
                with self.assertRaises(self.launcher.LauncherError):
                    self.launcher.requirements()

    def test_missing_or_modified_bundle_fails_before_creating_runtime(self):
        wheel = self.skill / "wheels" / f"pyyaml-{self.version}-py3-none-any.whl"
        original = wheel.read_bytes()
        for mode in ("missing", "modified"):
            with self.subTest(mode=mode):
                if mode == "missing":
                    wheel.unlink()
                else:
                    wheel.write_bytes(original + b"tampered")
                with patch.object(self.launcher.subprocess, "run") as run:
                    result, _, error = self.call("setup")
                self.assertEqual(result, 2)
                self.assertIn("Reinstall the complete Skill", error)
                self.assertIn("setup never downloads", error)
                self.assertFalse(self.cache.exists())
                run.assert_not_called()
                wheel.write_bytes(original)

    def test_bundle_hash_mismatch_is_not_bypassed_by_a_ready_cache(self):
        self.fake_environment()
        wheel = self.skill / "wheels" / f"pyyaml-{self.version}-py3-none-any.whl"
        wheel.write_bytes(wheel.read_bytes() + b"tampered")
        with patch.object(self.launcher.subprocess, "run") as run:
            result, _, error = self.call("doctor")
        self.assertEqual(result, 2)
        self.assertIn("SHA256 verification", error)
        run.assert_not_called()

    def test_linked_dependency_directory_or_wheel_is_rejected(self):
        wheel = self.skill / "wheels" / f"pyyaml-{self.version}-py3-none-any.whl"
        for resource in (wheel.parent, wheel):
            with self.subTest(resource=resource), \
                    patch.object(self.launcher.Path, "is_symlink", autospec=True,
                                 side_effect=lambda path: path == resource), \
                    patch.object(self.launcher.subprocess, "run") as run:
                result, _, error = self.call("setup")
                self.assertEqual(result, 2)
                self.assertIn("Bundled dependency cannot be a symlink or junction", error)
                run.assert_not_called()

    def test_cache_cannot_be_relative_or_inside_source_or_target(self):
        for root in ("relative cache", str(self.skill / "cache"), str(self.target / "cache")):
            with self.subTest(root=root), patch.dict(os.environ, {"REVIEW_MEMORY_CACHE": root}):
                result, _, error = self.call("setup")
                self.assertEqual(result, 2)
                self.assertIn("cache", error.lower())
        other = self.directory / "other project"
        with patch.dict(os.environ, {"REVIEW_MEMORY_CACHE": str(other / "cache")}):
            with self.assertRaises(self.launcher.LauncherError):
                self.launcher.cache_root(["--root", str(other), "doctor"])

    def test_platform_defaults_are_user_cache_directories(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(self.launcher.Path, "home", return_value=self.directory / "home"):
            for system, suffix in (
                    ("win32", (".cache", "review-memory")),
                    ("darwin", ("Library", "Caches", "review-memory")),
                    ("linux", (".cache", "review-memory"))):
                with self.subTest(system=system), patch.object(self.launcher.sys, "platform", system):
                    expected = self.directory.joinpath("home", *suffix)
                    self.assertEqual(self.launcher.cache_root([]), expected)

    def test_dependency_subprocess_does_not_inherit_python_or_pip_overrides(self):
        with patch.dict(os.environ, {"PYTHONPATH": "evil", "PYTHONHOME": "evil",
                                    "PIP_EXTRA_INDEX_URL": "https://secret.invalid",
                                    "PIP_CONFIG_FILE": "evil"}):
            env = self.launcher.clean_environment()
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PIP_EXTRA_INDEX_URL", env)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)

    def test_nonisolated_invocation_fails_before_loading_runtime(self):
        result = subprocess.run(
            [sys.executable, str(self.skill / "scripts" / "main.py"), "doctor"],
            cwd=self.target, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("isolated mode", result.stderr)


class InstalledLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(dir=PROJECT)
        cls.addClassCleanup(temporary.cleanup)
        cls.directory = Path(temporary.name).resolve()
        cls.skill = cls.directory / "installed skill with spaces"
        shutil.copytree(SKILL, cls.skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cls.launcher = load_launcher(cls.skill / "scripts" / "main.py")
        _, content, _ = cls.launcher.requirements()
        cls.key = cls.launcher.cache_key(content)
        cls.cache = cls.directory / "user cache"
        cls.environment = cls.cache / cls.key
        cls.target = cls.directory / "untrusted PR"
        cls.target.mkdir()
        for name in ("review_memory.py", "yaml.py", "sitecustomize.py", "usercustomize.py"):
            (cls.target / name).write_text('raise RuntimeError("Untrusted project code executed")')
        (cls.target / "review_memory").mkdir()
        (cls.target / "review_memory" / "__init__.py").write_text(
            'raise RuntimeError("Untrusted project package executed")')
        cls.env = dict(os.environ, REVIEW_MEMORY_CACHE=str(cls.cache),
                       REVIEW_MEMORY_HOME=str(cls.directory / "project memory"),
                       PYTHONPATH=str(cls.target), PYTHONHOME=str(cls.target))
        # A fresh cache and unreachable proxies exercise real setup without PyPI or pip-cache access.
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "http_proxy", "https_proxy", "all_proxy"):
            cls.env[name] = "http://127.0.0.1:1"
        cls.env.update(NO_PROXY="", no_proxy="", PIP_INDEX_URL="https://untrusted.invalid/simple")
        result = subprocess.run(
            [sys.executable, "-I", str(cls.skill / "scripts" / "main.py"), "setup"],
            cwd=cls.target, env=cls.env, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.launcher.require_ready(cls.environment, cls.key)
        python = cls.launcher.environment_python(cls.environment)
        result = subprocess.run(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            capture_output=True, text=True, check=True)
        site = Path(result.stdout.strip())
        (site / "review_memory").mkdir()
        (site / "review_memory" / "__init__.py").write_text(
            'raise RuntimeError("Unrelated global package executed")')
        (site / "review_memory.py").write_text('raise RuntimeError("Unrelated global module executed")')

    def invoke(self, *args, script=None):
        return subprocess.run(
            [sys.executable, "-I", str(script or self.skill / "scripts" / "main.py"), *args],
            cwd=self.target, env=self.env, capture_output=True, text=True)

    def test_bundled_runtime_wins_over_global_and_project_shadows(self):
        result = self.invoke("doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["root"], str(self.target))
        self.assertFalse(report["initialized"])
        result = self.invoke("packs", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["packs"])
        self.assertFalse((self.skill / "scripts" / "review_memory" / "__pycache__").exists())

    def test_ready_setup_reuses_offline_environment_and_help_works(self):
        result = self.invoke("setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        for arg in ("--help", "--version"):
            with self.subTest(arg=arg):
                result = self.invoke(arg)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.strip())

    def test_relocated_installed_skill_reuses_environment_without_moving_it(self):
        moved = self.directory / "relocated skill"
        shutil.copytree(self.skill, moved, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        original = self.directory / "original hidden"
        self.skill.rename(original)
        try:
            result = self.invoke("doctor", script=moved / "scripts" / "main.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["root"], str(self.target))
            self.assertTrue(self.environment.is_dir())
        finally:
            original.rename(self.skill)

    def test_symlink_installation_resolves_actual_skill(self):
        link = self.directory / "skill link"
        try:
            link.symlink_to(self.skill, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest(f"Directory symlinks are not permitted: {exc}")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:TEST_SKILL_LINK -Target $env:TEST_SKILL_TARGET | Out-Null"],
                env=dict(os.environ, TEST_SKILL_LINK=str(link), TEST_SKILL_TARGET=str(self.skill)),
                capture_output=True, text=True)
            if result.returncode:
                self.skipTest(f"Neither symlinks nor junctions are permitted: {result.stderr}")
        result = self.invoke("doctor", script=link / "scripts" / "main.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["root"], str(self.target))


if __name__ == "__main__":
    unittest.main()
