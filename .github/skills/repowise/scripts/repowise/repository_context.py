"""Bounded repository evidence from immutable Git objects, never project execution."""
from __future__ import annotations

import fnmatch
import hashlib
import heapq
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from .common import Error, git


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in value)):
        raise Error("Unsafe or unsupported repository path.")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or str(path) != value:
        raise Error("Unsafe or noncanonical repository path.")
    return value


def _matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) or
               (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))
               for pattern in patterns)


def _patterns(value):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise Error("Path patterns must be a list of strings.")
    return value


def _added_lines(patch):
    lines, current = set(), None
    for line in patch.split("\n"):
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            current = int(match.group(1))
        elif current is not None and line.startswith("+"):
            lines.add(current)
            current += 1
        elif current is not None and line.startswith(" "):
            current += 1
        elif line.startswith(("diff --git ", "--- ", "+++ ")):
            current = None
    return sorted(lines)


def _source_lines(text):
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


@dataclass(frozen=True)
class _Entry:
    mode: str
    kind: str
    oid: str
    size: int | None

    @property
    def regular(self):
        return self.kind == "blob" and self.mode in {"100644", "100755"}


def _tree(root, revision, label, gaps):
    if (not isinstance(revision, str) or not revision or revision.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_./@{}~^+-]+", revision)):
        raise Error("Invalid Git revision.")
    entries, rejected, sha, duplicate_paths = {}, 0, None, set()
    try:
        sha = git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", sha):
            raise Error("Git did not resolve a full commit SHA.")
        raw = git(root, "ls-tree", "-r", "-l", "-z", "--full-tree", sha, "--", binary=True)
    except (Error, UnicodeError) as exc:
        gaps.append(f"{label} tree context unavailable: {exc}")
        return sha, entries, False, rejected
    for row in raw.split(b"\0"):
        if not row:
            continue
        try:
            metadata, name = row.split(b"\t", 1)
            mode, kind, oid, size = metadata.decode("ascii").split()
            path = _path(name.decode("utf-8"))
            if (not re.fullmatch(r"[0-7]{6}", mode)
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", oid)
                    or kind not in {"blob", "commit", "tree"}):
                raise Error("Invalid Git tree entry metadata.")
            length = int(size) if size != "-" else None
            if (kind == "blob" and length is None) or (length is not None and length < 0):
                raise Error("Invalid Git blob size.")
            if path in entries or path in duplicate_paths:
                entries.pop(path, None)
                duplicate_paths.add(path)
                raise Error("Duplicate Git tree path.")
            entries[path] = _Entry(mode, kind, oid, length)
        except (Error, UnicodeError, ValueError) as exc:
            rejected += 1
            gaps.append(f"Unsafe or unavailable {label} tree entry not inspected: {row!r}: {exc}")
    return sha, entries, True, rejected


_SOURCE_EXTENSIONS = {
    ".rs", ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt",
    ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".rb", ".swift", ".scala",
}
_CONFIG_NAMES = {
    "cargo.toml", "cargo.lock", "rust-toolchain", "rust-toolchain.toml",
    "rustfmt.toml", ".rustfmt.toml", "clippy.toml", ".clippy.toml", ".editorconfig",
    ".gitattributes", "package.json", "pyproject.toml", "go.mod", "go.work",
    "tsconfig.json", "global.json", "directory.build.props", "directory.packages.props",
}
_MODULE_NAMES = {"lib.rs", "main.rs", "mod.rs"}
_GENERIC_WORDS = {"mod", "lib", "main", "index", "init", "impl", "test", "tests"}


def _ancestors(path):
    return list(PurePosixPath(path).parents)


def _role_words(path):
    stem = PurePosixPath(path).stem
    stem = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", stem)
    return set(re.findall(r"[a-z0-9]+", stem.lower())) - _GENERIC_WORDS


def _is_test(path):
    value = PurePosixPath(path)
    name = value.stem.lower()
    return (any(part in {"test", "tests", "__tests__"} for part in value.parts[:-1])
            or name.startswith("test_") or name.endswith(("_test", "_tests", ".test", ".spec")))


def _subsystem(path, manifests):
    for parent in _ancestors(path):
        if (parent / "Cargo.toml").as_posix() in manifests:
            return parent
    parts = PurePosixPath(path).parts[:-1]
    for index, part in enumerate(parts):
        if part in {"src", "lib", "app", "tests", "test", "__tests__"}:
            return PurePosixPath(*parts[:index])
    return PurePosixPath(*parts[:1])


def _distance(first, second):
    left, right = PurePosixPath(first).parent.parts, PurePosixPath(second).parent.parts
    common = 0
    for a, b in zip(left, right):
        if a != b:
            break
        common += 1
    return len(left) + len(right) - 2 * common


def _example_reason(path, anchors, manifests):
    value, matches = PurePosixPath(path), []
    name = value.name.lower()
    source = value.suffix.lower() in _SOURCE_EXTENSIONS
    words = _role_words(path)
    for anchor in anchors:
        other = PurePosixPath(anchor)
        parents = _ancestors(anchor)
        distance = _distance(path, anchor)
        same_role = bool(words & _role_words(anchor))
        same_subsystem = _subsystem(path, manifests) == _subsystem(anchor, manifests)
        if value.suffix == ".rs" and other.suffix == ".rs":
            if ((value.name in _MODULE_NAMES and value.parent in parents)
                    or (value.with_suffix("") in parents)):
                matches.append((1, distance, "ancestor Rust module root"))
        if ((name in _CONFIG_NAMES and value.parent in parents)
                or (name in {"config", "config.toml"} and value.parent.name == ".cargo"
                    and value.parent.parent in parents)):
            matches.append((2, distance, "ancestor dependency/toolchain/style configuration"))
        doc = (name.startswith(("readme", "architecture", "design", "contributing"))
               and value.suffix.lower() in {"", ".md", ".rst", ".txt", ".adoc"})
        if doc and (value.parent in parents or
                    (value.parent.name in {"docs", "doc", "architecture"}
                     and (value.parent.parent in parents or
                          value.parent.parent.name in {"docs", "doc"} and same_subsystem))):
            matches.append((2, distance, "nearby architecture/project documentation"))
        if not source:
            continue
        if _is_test(path) and same_role:
            matches.append((3, distance, "similarly named test (filename heuristic)"))
        if value.parent == other.parent and value.suffix == other.suffix:
            matches.append((4, int(not same_role), "sibling source implementation"))
        if _is_test(path) and same_subsystem and distance <= 4:
            matches.append((5, distance, "nearby subsystem test"))
        if same_role and (value.suffix == other.suffix or _is_test(path)):
            matches.append((6, distance, "similar source responsibility (filename heuristic)"))
        if same_subsystem and distance <= 3 and (
                value.suffix == other.suffix or other.suffix.lower() not in _SOURCE_EXTENSIONS):
            matches.append((7, distance, "nearby subsystem source (path heuristic)"))
    return min(matches) if matches else None


_RUST_RAW_STRING = re.compile(r'(?:br|r)(#*)"')
_RUST_CHARACTER = re.compile(
    r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\r\n])'"
)


def _rust_code(text):
    """Mask comments and literals without changing offsets, retaining plain string delimiters."""
    result, index = list(text), 0

    def mask(start, end):
        for offset in range(start, end):
            if result[offset] != "\n":
                result[offset] = " "

    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            mask(index, end)
        elif text.startswith("/*", index):
            end, depth = index + 2, 1
            while end < len(text) and depth:
                if text.startswith("/*", end):
                    depth += 1
                    end += 2
                elif text.startswith("*/", end):
                    depth -= 1
                    end += 2
                else:
                    end += 1
            mask(index, end)
        elif (raw := _RUST_RAW_STRING.match(text, index)) is not None:
            closing = '"' + raw.group(1)
            finish = text.find(closing, raw.end())
            end = len(text) if finish < 0 else finish + len(closing)
            mask(index, end)
        elif text[index] == '"':
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                elif text[end] == '"':
                    break
                else:
                    end += 1
            mask(index + 1, min(end, len(text)))
            end = min(end + 1, len(text))
        elif text[index] == "'" and (char := _RUST_CHARACTER.match(text, index)) is not None:
            end = char.end()
            mask(index, end)
        else:
            index += 1
            continue
        index = end
    return "".join(result)


_RUST_DECLARATION = re.compile(
    r'^[ \t]*(?:#\s*\[\s*path\s*=\s*"(?P<path>[^"\n]*)"\s*\]\s*)?'
    r'(?:pub(?:\s*\([^()\n]*\))?\s+)?'
    r'(?:mod\s+(?P<module>(?:r#)?[A-Za-z_][A-Za-z_0-9]*)\s*;'
    r'|use\s+(?P<use>[^;]+);)', re.MULTILINE,
)


def _rust_links(path, text, tree):
    value = PurePosixPath(path)
    if value.suffix != ".rs":
        return set(), 0
    code, links, unresolved = _rust_code(text), set(), 0
    module_dir = value.parent if value.name in _MODULE_NAMES else value.with_suffix("")
    crate_dir = next((p for p in value.parents
                      if any((p / name).as_posix() in tree for name in ("lib.rs", "main.rs"))), None)
    cursor, nesting = 0, 0
    for match in _RUST_DECLARATION.finditer(code):
        for char in code[cursor:match.start()]:
            if char in "{([":
                nesting += 1
            elif char in "})]":
                nesting -= 1
        cursor = match.start()
        if nesting != 0:
            continue
        wanted = []
        if match.group("module"):
            if match.group("path") is not None:
                literal = text[match.start("path"):match.end("path")]
                try:
                    wanted.append((value.parent / _path(literal)).as_posix())
                except Error:
                    unresolved += 1
                    continue
            else:
                stem = module_dir / match.group("module").removeprefix("r#")
                wanted.extend((stem.with_suffix(".rs").as_posix(), (stem / "mod.rs").as_posix()))
        else:
            prefix = re.match(
                r"\s*(crate|self|super)\b(\s*::\s*(?:r#)?[A-Za-z_][A-Za-z_0-9]*)*",
                match.group("use"),
            )
            if not prefix:
                continue
            parts = [p.strip().removeprefix("r#") for p in prefix.group().strip().split("::")]
            if parts[0] == "crate":
                directory = crate_dir
                parts = parts[1:]
            else:
                directory = module_dir
                if parts[0] == "self":
                    parts = parts[1:]
                while parts and parts[0] == "super":
                    if directory == PurePosixPath("."):
                        directory = None
                        break
                    directory = directory.parent
                    parts = parts[1:]
            if directory is None:
                unresolved += 1
                continue
            for part in parts:
                directory = directory / part
                wanted.extend((directory.with_suffix(".rs").as_posix(),
                               (directory / "mod.rs").as_posix()))
        available = {p for p in wanted if p in tree}
        links.update(available)
        if not available:
            unresolved += 1
    return links, unresolved


def capture_context(root, base, head, records, max_files, max_bytes, config, context_paths=None,
                    *, focus_paths=None):
    """Return (contexts, coverage gaps, budgeted raw bytes, JSON selection metadata)."""
    if any(type(limit) is not int or limit < 0 for limit in (max_files, max_bytes)):
        raise Error("Context file and byte limits must be nonnegative integers.")
    if not isinstance(config, dict):
        raise Error("Context configuration must be an object.")
    excluded = _patterns(config.get("exclusions", [])) + _patterns(config.get("generated_paths", []))
    if context_paths is not None and not isinstance(context_paths, list):
        raise Error("Context paths must be a list of canonical repository file paths.")
    requested = []
    for value in context_paths or []:
        path = _path(value)
        if any(char in path for char in "*?[]"):
            raise Error("Context paths must be literal file paths, without wildcards.")
        if path not in requested:
            requested.append(path)
    if focus_paths is not None and not isinstance(focus_paths, list):
        raise Error("Focus paths must be a list of canonical repository file paths.")
    focus = []
    for value in focus_paths or []:
        path = _path(value)
        if path not in focus:
            focus.append(path)
    normalized = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("status"), str) or not record["status"]:
            raise Error("Invalid context file record.")
        normalized.append(dict(record, path=_path(record.get("path")),
                               old_path=_path(record.get("old_path"))))

    gaps, contexts, consumed = [], [], 0
    base_sha, before_tree, before_available, before_rejected = _tree(root, base, "BASE", gaps)
    head_sha, after_tree, after_available, after_rejected = _tree(root, head, "HEAD", gaps)
    trees = {"before": before_tree, "after": after_tree}
    available = {"before": before_available, "after": after_available}
    candidates, aliases, omissions, processed, queue = {}, {}, {}, set(), []
    anchor_records = {}
    for record in normalized:
        if (record["status"] != "context" and not _matches(record["path"], excluded)
                and not _matches(record["old_path"], excluded)):
            anchor_records.setdefault(record["path"], record)
    anchors = sorted({r[key] for r in list(anchor_records.values())[:max_files]
                      for key in ("path", "old_path")} | set(focus[:max_files]))
    manifests = {p for p in before_tree.keys() | after_tree.keys()
                 if PurePosixPath(p).name == "Cargo.toml"}

    def add(record, rank, reason):
        path = record["path"]
        if record["status"] == "context":
            path = aliases.get(path, path)
        if path not in candidates:
            candidates[path] = {"record": record, "rank": rank, "reasons": {reason}}
            heapq.heappush(queue, (rank, path))
        else:
            if rank < candidates[path]["rank"]:
                candidates[path]["rank"] = rank
                heapq.heappush(queue, (rank, path))
            candidates[path]["reasons"].add(reason)

    def supplemental(path, rank, reason):
        add({"path": path, "old_path": path, "status": "context"}, rank, reason)

    def omit(path, kind, message, side=None):
        detail = {"kind": kind, "message": message}
        if side is not None:
            detail["side"] = side
        details = omissions.setdefault(path, [])
        if detail not in details:
            details.append(detail)
            gaps.append(message)

    for index, record in enumerate(r for r in normalized if r["status"] != "context"):
        add(record, (0, index), "changed file")
    for record in normalized:
        if (record["status"].startswith("R") and record["old_path"] != record["path"]
                and record["old_path"] not in candidates):
            aliases[record["old_path"]] = record["path"]
    for index, path in enumerate(requested):
        supplemental(path, (1, index), f"explicit requested path: {path}")
    for index, record in enumerate(r for r in normalized if r["status"] == "context"):
        add(record, (1, len(requested) + index), "supplied context record")
    for path in sorted(after_tree):
        if PurePosixPath(path).name == "Cargo.toml":
            supplemental(path, (2, 0), "required HEAD workspace Cargo manifest")
    for path in sorted(before_tree.keys() | after_tree.keys()):
        reason = _example_reason(path, anchors, manifests)
        if reason is not None:
            precedent = int(not (path in before_tree and before_tree[path].regular))
            supplemental(path, (3, precedent, reason[0], reason[1]), reason[2])

    unresolved_links = 0
    while queue:
        rank, path = heapq.heappop(queue)
        candidate = candidates[path]
        if path in processed or rank != candidate["rank"]:
            continue
        record = candidate["record"]
        processed.add(path)
        if _matches(record["path"], excluded) or _matches(record["old_path"], excluded):
            label = "changed file" if record["status"] != "context" else "context"
            omit(path, "excluded", f"Excluded/generated {label}: {path} (before path: {record['old_path']})")
            continue
        if len(contexts) >= max_files:
            omit(path, "file_limit", f"File limit omitted context: {path}")
            continue
        item = dict(record, before=None, after=None, before_blob=None, after_blob=None, changed_lines=[])
        for side, source_path in (("before", record["old_path"]), ("after", record["path"])):
            if (side == "before" and record["status"] == "A"
                    or side == "after" and record["status"] == "D"):
                continue
            entry = trees[side].get(source_path)
            if entry is None:
                other = "after" if side == "before" else "before"
                other_path = record["path"] if other == "after" else record["old_path"]
                if (record["status"] == "context" and available[side]
                        and other_path in trees[other]):
                    continue
                message = "path absent from immutable tree" if available[side] else "tree metadata unavailable"
                omit(path, "unavailable", f"Unavailable {side} source {source_path}: {message}", side)
                continue
            if not entry.regular:
                omit(path, "non_regular",
                     f"Non-regular {side} source not inspected: {source_path} (mode {entry.mode}, {entry.kind})", side)
                continue
            if consumed + entry.size > max_bytes:
                omit(path, "byte_limit", f"Byte limit omitted {side} source: {source_path}", side)
                continue
            consumed += entry.size
            try:
                raw = git(root, "cat-file", "blob", entry.oid, binary=True)
                if len(raw) != entry.size:
                    raise Error("Blob length did not match immutable tree metadata.")
                hasher = hashlib.sha1() if len(entry.oid) == 40 else hashlib.sha256()
                hasher.update(f"blob {len(raw)}\0".encode("ascii"))
                hasher.update(raw)
                if hasher.hexdigest() != entry.oid:
                    raise Error("Blob content did not match its immutable object ID.")
                if b"\0" in raw:
                    omit(path, "binary", f"Binary {side} source not inspected: {source_path}", side)
                    continue
                item[side] = raw.decode("utf-8")
                item[f"{side}_blob"] = entry.oid
            except (Error, UnicodeError) as exc:
                omit(path, "unavailable", f"Unavailable {side} source {source_path}: {exc}", side)
        if (record["status"] != "context" and item["after"] is not None
                and (item["before"] is not None or record["status"] == "A")):
            try:
                if record["status"] == "A":
                    item["changed_lines"] = list(range(1, len(_source_lines(item["after"])) + 1))
                elif item["before_blob"] != item["after_blob"]:
                    # Blob-to-blob diff cannot pull in another path's unbudgeted rename/reuse content.
                    patch = git(root, "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv",
                                "--no-color", "--text", "--unified=0", item["before_blob"],
                                item["after_blob"], "--", binary=True)
                    item["changed_lines"] = _added_lines(patch.decode("utf-8"))
            except (Error, UnicodeError) as exc:
                omit(path, "changed_lines", f"Changed-line context unavailable for {path}: {exc}")
        contexts.append(item)
        for side, source_path in (("before", record["old_path"]), ("after", record["path"])):
            if item[side] is not None:
                links, unresolved = _rust_links(source_path, item[side], trees[side])
                unresolved_links += unresolved
                for linked in sorted(links):
                    precedent = int(not (linked in before_tree and before_tree[linked].regular))
                    supplemental(linked, (3, precedent, 0, _distance(linked, source_path)),
                                 f"static Rust relationship from {side} {source_path}")

    selected = []
    for item in contexts:
        reasons = sorted(candidates[item["path"]]["reasons"])
        item["selection_reasons"] = reasons
        selected.append({"path": item["path"], "old_path": item["old_path"], "status": item["status"],
                         "reasons": reasons,
                         "captured_versions": [name for side, name in (("before", "BASE"), ("after", "HEAD"))
                                               if item[side] is not None]})
    counts = Counter(detail["kind"] for details in omissions.values()
                     for detail in {d["kind"]: d for d in details}.values())
    selection = {
        "strategy": "immutable-repository-first-v1",
        "requested_paths": requested,
        "selected_paths": [item["path"] for item in contexts],
        "selected": selected,
        "search_scope": {
            "base_sha": base_sha, "head_sha": head_sha,
            "base_tree_available": before_available, "head_tree_available": after_available,
            "base_tree_entries": len(before_tree), "head_tree_entries": len(after_tree),
            "rejected_tree_entries": before_rejected + after_rejected,
            "anchors": anchors, "unresolved_static_rust_links": unresolved_links,
            "focus_paths": focus,
            "inventory": "Recursive BASE/HEAD tree metadata; not repository-wide source-content inspection.",
        },
        "limits": {"max_files": max_files, "max_bytes": max_bytes, "consumed_bytes": consumed},
        "considered_count": len(candidates),
        "omitted_count": len(omissions),
        "omission_counts": dict(sorted(counts.items())),
        "omissions": [{"path": path, "details": details} for path, details in sorted(omissions.items())],
        "limitations": [
            "Existing code is evidence, not approved policy; HEAD-only code cannot establish a BASE convention.",
            "Selection is bounded and heuristic, not a compiler, call graph, semantic search or exhaustive review. "
            "Filename/path search uses at most max_files distinct eligible changed-file anchors.",
            "Changed files, explicit paths and all HEAD Cargo manifests precede automatic examples; all share limits.",
            "Automatic examples prefer BASE precedents, then static Rust links, module roots, configuration/docs "
            "and filename/path-ranked siblings/tests; relevant distant or differently named implementations may be missed.",
            "Rust links use captured text only: simple top-level mod, adjacent literal #[path], and "
            "crate/self/super use prefixes. Inline modules, grouped import members, macros, cfg evaluation, "
            "noncanonical path attributes and Cargo target resolution are not resolved.",
            "Tree metadata is enumerated for both commits; only selected full regular UTF-8 blobs are inspected. "
            "No mutable worktree files, symlink targets, submodules or project commands are inspected or executed.",
            "File/byte limits bound captured source context, not Git's internal metadata or rename-analysis work.",
            "Selected paths allocate file slots, not successful checks; captured_versions and gaps describe missing "
            "evidence. omitted_count counts distinct paths with any omitted source or changed-line evidence. "
            "Expected one-sided absence of supplemental context is not an omission.",
            "Git must support disabling lazy fetches; unavailable objects or unsupported Git options remain gaps "
            "rather than triggering network access or target writes.",
        ],
    }
    return contexts, list(dict.fromkeys(gaps)), consumed, selection
