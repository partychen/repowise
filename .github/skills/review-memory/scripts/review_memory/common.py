from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from . import __version__


class Error(ValueError):
    """An actionable input, trust, or coverage error."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise Error("Expected an RFC 3339 timestamp.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Error(f"Invalid timestamp: {value}") from exc
    if result.tzinfo is None:
        raise Error(f"Timestamp must include timezone: {value}")
    return result


def canonical_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Error(f"Cannot read JSON {path}: {exc}") from exc


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise Error("YAML keys must be unique strings.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def parse_yaml(text: str):
    if len(text.encode("utf-8")) > 2_000_000:
        raise Error("YAML exceeds the 2 MB limit.")
    try:
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)):
            raise Error("YAML anchors and aliases are not supported.")
        result = yaml.load(text, Loader=UniqueLoader)
        canonical_bytes(result)
        return result
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise Error(f"Invalid YAML/JSON-compatible data: {exc}") from exc


def load_yaml(path: Path):
    try:
        return parse_yaml(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Error(f"Cannot read YAML {path}: {exc}") from exc


def safe_path(root: Path, relative: str | Path) -> Path:
    raw = str(relative)
    portable = PurePosixPath(raw.replace("\\", "/"))
    if portable.is_absolute() or ".." in portable.parts or ":" in raw or "\x00" in raw:
        raise Error(f"Path outside allowed directory: {relative}")
    root = root.resolve()
    result = root.joinpath(*portable.parts)
    cursor = root
    for part in portable.parts:
        cursor = cursor / part
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise Error(f"Symlinks and junctions are not allowed: {relative}")
    if not result.resolve().is_relative_to(root):
        raise Error(f"Path outside allowed directory: {relative}")
    return result


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".review_memory-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, obj):
    atomic_write(path, (json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def write_yaml(path: Path, obj):
    atomic_write(path, yaml.safe_dump(obj, sort_keys=False, allow_unicode=False).encode())


@contextmanager
def lock(root: Path):
    path = safe_path(root, ".review/local/write.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Error(f"Another writer holds {path}; inspect it before removing a stale lock.") from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(f"pid={os.getpid()}\ntime={utcnow()}\n")
        yield
    finally:
        path.unlink()


def git(root: Path, *args: str, binary=False):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "--no-pager", *args],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error(f"Git unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise Error(f"Git {args[0] if args else ''} failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="strict").strip()


def resolve_commit(root: Path, ref: str) -> str:
    if not ref or ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./@{}~^+-]+", ref):
        raise Error("Invalid Git revision.")
    sha = git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not re.fullmatch(r"[a-f0-9]{40,64}", sha):
        raise Error("Git did not resolve a full commit SHA.")
    return sha


def runtime_hash() -> str:
    directory = Path(__file__).parent
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.py"))})


def runtime_info() -> dict:
    return {"version": __version__, "hash": runtime_hash(),
            "python": sys.version.split()[0], "pyyaml": yaml.__version__}
