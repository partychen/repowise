"""Isolated, stdlib-only launcher for the installed repowise Skill."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.stderr.write("repowise: launch with Python 3.11+ in isolated mode: "
                     'python -I "<installed skill>/scripts/main.py" setup\n')
    raise SystemExit(2)

import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve()
SKILL = SCRIPT.parents[1]
CACHE_SCHEMA = 2
VENV_TIMEOUT = 120
INSTALL_TIMEOUT = 120
PROBE_TIMEOUT = 30


class LauncherError(Exception):
    pass


def requirements():
    path = SKILL / "requirements.txt"
    content = path.read_bytes()
    declared = [line.strip() for line in content.decode("utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    match = re.fullmatch(r"PyYAML==(6\.\d+\.\d+) --hash=sha256:([a-f0-9]{64})",
                         declared[0]) if len(declared) == 1 else None
    if match is None:
        raise LauncherError("The installed Skill must declare exactly one pinned PyYAML 6.x "
                            "dependency with its bundled wheel's SHA256 hash.")
    version, expected_hash = match.groups()
    if tuple(int(part) for part in version.split(".")) < (6, 0, 2):
        raise LauncherError("The pinned PyYAML version must be at least 6.0.2.")
    wheel = SKILL / "wheels" / f"pyyaml-{version}-py3-none-any.whl"
    recovery = "Reinstall the complete Skill from a trusted source; setup never downloads dependencies."
    for resource in (wheel.parent, wheel):
        if resource.is_symlink() or (hasattr(resource, "is_junction") and resource.is_junction()):
            raise LauncherError(f"Bundled dependency cannot be a symlink or junction: {resource}. {recovery}")
    try:
        actual_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    except OSError as exc:
        raise LauncherError(f"Bundled PyYAML wheel is missing or unreadable: {wheel}. {recovery}") from exc
    if actual_hash != expected_hash:
        raise LauncherError(f"Bundled PyYAML wheel failed SHA256 verification: {wheel}. {recovery}")
    return path, content, version


def cache_key(content):
    identity = {
        "schema": CACHE_SCHEMA,
        "python": list(sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "abi": sys.implementation.cache_tag,
        "platform": sys.platform,
        "machine": platform.machine(),
        "base_executable": str(Path(sys._base_executable).resolve()),
        "requirements": hashlib.sha256(content).hexdigest(),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def cache_root(argv):
    override = os.environ.get("REPOWISE_CACHE")
    if override:
        root = Path(override).expanduser()
    elif sys.platform == "win32":
        # Store Python virtualizes LocalAppData into a much longer package path.
        root = Path.home() / ".cache" / "repowise"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "repowise"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "repowise"
    if not root.is_absolute():
        raise LauncherError("REPOWISE_CACHE / platform cache location must be an absolute path.")
    root = root.resolve()
    protected = [SKILL]
    if SKILL.parent.name == "skills" and SKILL.parent.parent.name == ".github":
        protected.append(SKILL.parents[2])
    cwd = Path.cwd().resolve()
    if cwd not in (Path.home().resolve(), Path(cwd.anchor)):
        protected.append(cwd)
    for index, arg in enumerate(argv):
        if arg == "--root" and index + 1 < len(argv):
            protected.append(Path(argv[index + 1]).resolve())
        elif arg.startswith("--root="):
            protected.append(Path(arg.partition("=")[2]).resolve())
    if any(root.is_relative_to(path) for path in protected):
        raise LauncherError("Runtime cache must be outside the target project and installed Skill/source. "
                            "Set REPOWISE_CACHE to an absolute, private user-cache directory.")
    return root


def environment_python(environment):
    return environment / "Scripts" / "python.exe" if os.name == "nt" else environment / "bin" / "python"


def setup_command():
    return f'python -I "{SCRIPT}" setup'


def repair_message(environment):
    return (f"Remove only the incomplete/corrupt runtime directory {environment} "
            f"(after confirming no command is using it), then run: {setup_command()}")


def require_ready(environment, key):
    try:
        marker = json.loads((environment / "ready.json").read_text(encoding="utf-8"))
        configuration = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise LauncherError(f"Runtime is not ready. {repair_message(environment)}") from exc
    if (marker != {"key": key, "environment": str(environment)}
            or environment.resolve() != environment
            or not environment_python(environment).is_file()
            or not re.search(r"(?mi)^include-system-site-packages\s*=\s*false\s*$", configuration)):
        raise LauncherError(f"Runtime cache validation failed. {repair_message(environment)}")


def clean_environment():
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PYTHON", "PIP_"))}
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def diagnostics(text):
    return re.sub(r"(?i)((?:https?|socks5h?)://)[^\s/@]+@", r"\1[redacted]@", text)


def checked_run(command, *, cwd, stage, timeout):
    print(f"repowise: {stage} (timeout: {timeout}s)...", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(command, cwd=cwd, env=clean_environment(),
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        parts = [part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part
                 for part in (exc.stdout, exc.stderr) if part]
        details = diagnostics("\n".join(parts).strip())
        raise LauncherError(f"{stage} timed out after {timeout}s."
                            + (f"\n{details}" if details else "")) from exc
    if result.returncode:
        details = diagnostics("\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip()))
        raise LauncherError(f"{stage} failed (exit {result.returncode}).\n{details}")
    print(f"repowise: {stage} complete.", file=sys.stderr, flush=True)


def probe_environment(environment, version):
    code = (
        "import pathlib, sys, yaml; "
        f"assert pathlib.Path(sys.prefix).resolve() == pathlib.Path({str(environment)!r}), 'wrong virtualenv'; "
        "assert sys.prefix != sys.base_prefix, 'not a virtualenv'; "
        f"assert yaml.__version__ == {version!r}, 'wrong PyYAML version'; "
        "assert pathlib.Path(yaml.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()), "
        "'PyYAML is outside the runtime virtualenv'"
    )
    checked_run([str(environment_python(environment)), "-I", "-c", code], cwd=environment,
                stage="Checking cached Python and PyYAML", timeout=PROBE_TIMEOUT)


def setup(environment, key, requirement_path, version):
    environment.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    environment = environment.parent.resolve() / environment.name
    lock = environment.with_suffix(".lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise LauncherError(f"Runtime setup is locked: {lock}. Wait for setup to finish; "
                            "remove this lock only after confirming no setup process is active.") from exc
    try:
        if environment.is_symlink() or (hasattr(environment, "is_junction") and environment.is_junction()):
            raise LauncherError(f"Runtime directory cannot be a symlink or junction. {repair_message(environment)}")
        if environment.exists():
            require_ready(environment, key)
            try:
                probe_environment(environment, version)
            except LauncherError as exc:
                raise LauncherError(f"{exc}\n{repair_message(environment)}") from exc
        else:
            try:
                checked_run([sys.executable, "-I", "-m", "venv", str(environment)],
                            cwd=environment.parent, stage="Creating isolated virtualenv",
                            timeout=VENV_TIMEOUT)
                checked_run([
                    str(environment_python(environment)), "-I", "-m", "pip", "--isolated",
                    "--disable-pip-version-check", "install", "--no-input", "--no-deps",
                    "--no-index", "--no-cache-dir", "--only-binary=:all:", "--require-hashes",
                    "--find-links", str(requirement_path.parent / "wheels"),
                    "--requirement", str(requirement_path),
                ], cwd=environment, stage=f"Installing bundled PyYAML {version} offline",
                    timeout=INSTALL_TIMEOUT)
                probe_environment(environment, version)
                (environment / "ready.json").write_text(
                    json.dumps({"key": key, "environment": str(environment)}), encoding="utf-8")
            except LauncherError as exc:
                raise LauncherError(f"{exc}\n{repair_message(environment)}") from exc
    finally:
        lock.rmdir()
    print(f"repowise runtime ready: {environment}")
    return 0


def run_bundled(environment, version, argv):
    try:
        import yaml
    except ImportError as exc:
        raise LauncherError(f"Cached PyYAML cannot be imported. {repair_message(environment)}") from exc
    if (getattr(yaml, "__version__", None) != version
            or not Path(yaml.__file__).resolve().is_relative_to(environment)):
        raise LauncherError(f"Cached PyYAML does not match the declared dependency. {repair_message(environment)}")
    package = SCRIPT.parent / "repowise"
    if not (package / "__init__.py").is_file() or not (package / "cli.py").is_file():
        raise LauncherError("Installed Skill is missing its bundled runtime; reinstall the Skill.")
    # Bind the package explicitly, never to a same-named global or target-project module.
    for name in list(sys.modules):
        if name == "repowise" or name.startswith("repowise."):
            del sys.modules[name]
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        "repowise", package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["repowise"] = module
    spec.loader.exec_module(module)
    from repowise.cli import main
    return main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if sys.version_info < (3, 11):
            raise LauncherError("Python 3.11 or newer is required.")
        requirement_path, content, version = requirements()
        key = cache_key(content)
        environment = cache_root(argv) / key
        if argv and argv[0] == "setup":
            if argv[1:] in (["--help"], ["-h"]):
                print("setup: prepare/reuse a private cached virtualenv using only the Skill's "
                      "bundled, hash-verified dependency wheel; no network downloads.")
                return 0
            if len(argv) != 1:
                raise LauncherError("setup takes no arguments.")
            return setup(environment, key, requirement_path, version)
        if not environment.exists():
            raise LauncherError(f"Runtime setup is required. Run: {setup_command()}")
        if environment.with_suffix(".lock").exists():
            raise LauncherError("Runtime setup is in progress or its lock is stale. "
                                f"Run {setup_command()} for lock recovery instructions.")
        require_ready(environment, key)
        if Path(sys.prefix).resolve() == environment and sys.prefix != sys.base_prefix:
            return run_bundled(environment, version, argv)
        result = subprocess.run(
            [str(environment_python(environment)), "-I", str(SCRIPT), *argv],
            env=clean_environment())
        return result.returncode
    except (LauncherError, OSError, UnicodeError) as exc:
        print(f"repowise: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
