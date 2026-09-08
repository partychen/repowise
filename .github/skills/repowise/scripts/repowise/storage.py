"""Keep mutable project memory outside code checkouts and Skill installations."""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .common import Error, is_link, load_json, load_yaml, safe_path, write_json


def _canonical(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _protected_roots() -> list[Path]:
    package = Path(__file__).resolve().parent
    protected = [package]
    if package.parent.name == "scripts":
        skill = package.parent.parent
        protected.append(skill)
        if skill.parent.name == "skills" and skill.parent.parent.name == ".github":
            protected.append(skill.parents[2])
    return protected


def _reject_link(path: Path):
    if is_link(path):
        raise Error(f"Project storage cannot be a symlink or junction: {path}")


def validate_memory_root(target_root: Path, memory_root: Path) -> Path:
    target = Path(target_root).expanduser().resolve()
    if not target.is_dir():
        raise Error("Target root must be an existing directory.")
    raw = Path(memory_root).expanduser()
    if not raw.is_absolute():
        raise Error("Project storage must be an absolute path.")
    _reject_link(raw)
    memory = raw.resolve()
    if memory.is_relative_to(target) or target.is_relative_to(memory):
        raise Error("Project storage and target repository must be separate, non-overlapping directories.")
    if any(memory.is_relative_to(path) or path.is_relative_to(memory) for path in _protected_roots()):
        raise Error("Project storage must be outside the installed Skill and its source checkout.")
    if memory.exists() and not memory.is_dir():
        raise Error("Project storage must be a directory.")
    safe_path(memory, ".review")
    return memory


@dataclass(frozen=True)
class ProjectStorage:
    target_root: Path
    data_home: Path
    storage_root: Path
    project_id: str

    def describe(self) -> dict:
        return {
            "target_root": str(self.target_root),
            "data_home": str(self.data_home),
            "storage_root": str(self.storage_root),
            "project_id": self.project_id,
            "local_path": str(safe_path(self.storage_root, ".review/local")),
            "paths_relative_to": str(self.storage_root),
        }

    def binding(self, repository: str) -> dict:
        return {
            "schema_version": 1,
            "project_id": self.project_id,
            "target_root": _canonical(self.target_root),
            "repository": repository,
        }

    def arguments(self, command: str) -> list[str]:
        return ["--root", str(self.target_root), "--data-home", str(self.data_home), command]


def project_storage(target_root: Path, data_home: Path | None = None) -> ProjectStorage:
    target = Path(target_root).expanduser().resolve()
    if not target.is_dir():
        raise Error("Target root must be an existing directory.")
    if data_home is None:
        override = os.environ.get("REPOWISE_HOME")
        if override is not None and not override.strip():
            raise Error("REPOWISE_HOME must be a nonempty absolute path.")
        data_home = Path(override) if override is not None else Path.home() / ".repowise" / "projects"
    home = Path(data_home).expanduser()
    if not home.is_absolute():
        raise Error("--data-home / REPOWISE_HOME must be an absolute path.")
    home = home.resolve()
    if home.is_relative_to(target) or any(home.is_relative_to(path) for path in _protected_roots()):
        raise Error("The data home must be outside the target repository and installed Skill/source.")
    name = re.sub(r"[^a-z0-9._-]+", "-", target.name.lower()).strip(".-")[:48] or "project"
    key = hashlib.sha256(_canonical(target).encode("utf-8")).hexdigest()[:24]
    project_id = f"{name}-{key}"
    memory = validate_memory_root(target, home / project_id)
    return ProjectStorage(target, home, memory, project_id)


@contextmanager
def _initialization_lock(storage: ProjectStorage):
    storage.data_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = safe_path(storage.data_home, f".{storage.project_id}.lock")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Error(f"Project initialization is locked: {path}. Remove only a stale lock "
                    "after confirming no initialization process is active.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        path.unlink()


def initialize_project(storage: ProjectStorage, repository: str) -> dict:
    from .core import initialize, validate_repository

    validate_repository(repository)
    repository = repository.lower()
    with _initialization_lock(storage):
        validate_memory_root(storage.target_root, storage.storage_root)
        storage.storage_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        binding_path = safe_path(storage.storage_root, ".review/project.json")
        expected = storage.binding(repository)
        if binding_path.exists():
            if load_json(binding_path) != expected:
                raise Error("External project binding differs from this target or repository; "
                            "do not reuse another project's storage.")
        else:
            if safe_path(storage.storage_root, ".review/config.yaml").exists():
                raise Error("Existing external state has no project binding; use a new data home "
                            "rather than implicitly trusting or overwriting it.")
            write_json(binding_path, expected)
        return initialize(storage.storage_root, repository)


def project_config(storage: ProjectStorage) -> dict:
    from .core import validate_config

    validate_memory_root(storage.target_root, storage.storage_root)
    configuration = safe_path(storage.storage_root, ".review/config.yaml")
    if not configuration.is_file():
        raise Error("This target has no external project configuration. "
                    "Run sync --repository owner/repo or init --repository owner/repo first.")
    config = load_yaml(configuration)
    validate_config(config)
    binding = safe_path(storage.storage_root, ".review/project.json")
    if not binding.is_file() or load_json(binding) != storage.binding(config["repository"]):
        raise Error("External project binding is missing or changed; "
                    "the stored knowledge cannot be associated with this target.")
    return config
