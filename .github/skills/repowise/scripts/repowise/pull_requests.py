"""Resolve a requested GitHub PR to local immutable code, without checkout or fetch."""
from __future__ import annotations

from urllib.parse import urlsplit

from .collect import _GitHub, _Store, _number, _repository
from .common import Error, digest, git, resolve_commit, utcnow
from .storage import validate_memory_root


def pull_request_number(value, repository):
    repository = _repository(repository)
    if type(value) is int:
        return _number(value, "PR number")
    if not isinstance(value, str):
        raise Error("PR must be a positive number or a GitHub pull-request URL.")
    if value.isascii() and value.isdecimal():
        return _number(int(value), "PR number")
    try:
        url = urlsplit(value)
    except ValueError as exc:
        raise Error("Invalid GitHub pull-request URL.") from exc
    parts = url.path.strip("/").split("/")
    if (url.scheme != "https" or url.netloc.lower() != "github.com"
            or len(parts) != 4 or parts[2] != "pull"
            or not parts[3].isascii() or not parts[3].isdecimal()):
        raise Error("PR must be a positive number or https://github.com/owner/repo/pull/number.")
    if _repository("/".join(parts[:2])) != repository:
        raise Error("PR URL belongs to a different project repository.")
    return _number(int(parts[3]), "PR number")


def resolve_pull_request(target_root, memory_root, repository, value):
    repository = _repository(repository)
    memory_root = validate_memory_root(target_root, memory_root)
    number = pull_request_number(value, repository)
    store = _Store(memory_root, repository)
    metadata = _GitHub(repository, store).pr(number)
    if type(metadata.get("number")) is not int or metadata["number"] != number:
        raise Error("PR metadata does not match the requested number.")
    base, head = metadata.get("base"), metadata.get("head")
    if (not isinstance(base, dict) or not isinstance(head, dict)
            or not isinstance(base.get("repo"), dict)
            or _repository(base["repo"].get("full_name")) != repository):
        raise Error("PR metadata does not bind its base repository to this project.")
    for side in (base, head):
        sha = side.get("sha")
        if (not isinstance(sha, str) or len(sha) not in (40, 64)
                or any(character not in "0123456789abcdef" for character in sha)):
            raise Error("PR metadata must contain full immutable base and head commit SHAs.")
    try:
        remote_base = resolve_commit(target_root, base["sha"])
        head_sha = resolve_commit(target_root, head["sha"])
        ancestors = git(target_root, "merge-base", "--all", remote_base, head_sha).splitlines()
    except Error as exc:
        raise Error("PR code objects are unavailable locally. Review never checks out or fetches into "
                    "the target; obtain these commits through a separately authorized Git workflow, "
                    f"then retry (base {base['sha']}, head {head['sha']}).") from exc
    if len(ancestors) != 1:
        raise Error("PR has no unique local merge base; choose explicit --base and --head revisions.")
    base_sha = resolve_commit(target_root, ancestors[0])
    title, body = metadata.get("title"), metadata.get("body") or ""
    if not isinstance(title, str) or not isinstance(body, str):
        raise Error("PR title and body must be text.")
    gaps, text = [], {}
    for key, value, limit in (("title", title, 4000), ("body", body, 60000)):
        encoded = value.encode("utf-8")
        text[key] = encoded[:limit].decode("utf-8", errors="ignore")
        if len(encoded) > limit:
            gaps.append(f"PR {key} context truncated; {len(encoded) - len(text[key].encode('utf-8'))} bytes omitted.")
    return {
        "repository": repository, "number": number,
        "url": f"https://github.com/{repository}/pull/{number}",
        "base_sha": base_sha, "remote_base_sha": remote_base, "head_sha": head_sha,
        "observed_at": utcnow(), "metadata_hash": digest(metadata), "raw_paths": store.raw,
        "authority": "untrusted_change_description", "coverage_gaps": gaps,
        **text,
    }


def prepare_pull_request_review(target_root, repository, value, trusted_ref, *, memory_root, **options):
    from .review import prepare_review

    context = resolve_pull_request(target_root, memory_root, repository, value)
    return prepare_review(target_root, repository, context["base_sha"], context["head_sha"],
                          trusted_ref, memory_root=memory_root, pr_context=context, **options)
