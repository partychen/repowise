"""Build a portable Skill archive from explicit source files, never local state."""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PROJECT = Path(__file__).resolve().parents[1]


def build_archive(project: Path, output: Path) -> dict:
    project = project.resolve()
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
        raise ValueError("Version cannot be used as a release filename.")
    skill = project / ".github" / "skills" / "review-memory"
    required = [skill / "SKILL.md", skill / "requirements.txt",
                skill / "scripts" / "main.py", skill / "packs" / "__init__.py"]
    paths = list(required)
    for pattern in ("references/*.md", "packs/*.json", "packs/sources/*.json", "scripts/review_memory/*.py"):
        paths.extend(skill.glob(pattern))
    members = {}
    for source in sorted(set(paths)):
        relative = source.relative_to(skill)
        ancestors = [source, *source.parents[:len(relative.parts)]]
        if any(path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) for path in ancestors):
            raise ValueError(f"Release source cannot be a symlink or junction: {relative}")
        if not source.resolve().is_relative_to(skill.resolve()):
            raise ValueError(f"Release source escaped the skill directory: {relative}")
        members["review-memory/" + relative.as_posix()] = source.read_bytes()
    members["review-memory/THIRD_PARTY.md"] = (project / "THIRD_PARTY.md").read_bytes()
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(members.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    payload = buffer.getvalue()
    filename = f"review-memory-skill-{version}.zip"
    sha256 = hashlib.sha256(payload).hexdigest()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / filename
    checksum_path = output / (filename + ".sha256")
    outputs = {
        archive_path: payload,
        checksum_path: f"{sha256}  {filename}\n".encode("ascii"),
    }
    for path, content in outputs.items():
        if path.exists() and path.read_bytes() != content:
            raise ValueError(f"Refusing to replace a different release artifact: {path}; bump the version or choose a clean output directory.")
    for path, content in outputs.items():
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(content)
    return {"archive": str(archive_path), "checksum": str(checksum_path), "sha256": sha256,
            "file_count": len(members)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist")
    args = parser.parse_args(argv)
    try:
        result = build_archive(PROJECT, args.output)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
