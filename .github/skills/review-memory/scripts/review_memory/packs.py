from __future__ import annotations

import re
from collections import Counter
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

from .common import Error, digest, load_json


def _directory() -> Path:
    directory = Path(__file__).resolve().parents[2] / "packs"
    if not directory.is_dir():
        directory = Path(str(files("review_memory_packs")))
    return directory


def _texts(values, label, *, empty=False):
    if not isinstance(values, list) or (not empty and not values) or any(
            not isinstance(value, str) or not value.strip() for value in values):
        raise Error(f"{label} must be a list of nonempty strings.")


def validate_pack(pack: dict) -> dict:
    if not isinstance(pack, dict) or type(pack.get("schema_version")) is not int or pack["schema_version"] != 1:
        raise Error("Reference pack requires schema_version 1.")
    if pack.get("authority") != "reference":
        raise Error("Packs cannot grant repository policy authority.")
    if not isinstance(pack.get("id"), str) or not re.fullmatch(r"[a-z0-9-]+", pack["id"]):
        raise Error("Invalid reference pack ID.")
    for field in ("title", "language", "summary"):
        if not isinstance(pack.get(field), str) or not pack[field].strip():
            raise Error(f"Pack {field} must be nonempty text.")
    _texts(pack.get("tags"), "Pack tags")
    if not isinstance(pack.get("sources"), list) or not pack["sources"]:
        raise Error("Reference packs require sources.")
    source_ids = set()
    for source in pack["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            raise Error("Each source requires a URL.")
        url = urlparse(source["url"])
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise Error("Reference sources must use credential-free HTTPS URLs.")
        if not isinstance(source.get("title"), str) or not source["title"].strip():
            raise Error("Each source requires a title.")
        if source.get("kind") == "external_skill":
            repository, revision, path = source.get("repository"), source.get("revision"), source.get("path")
            if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise Error("External source requires repository identity.")
            if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
                raise Error("External skill sources require a pinned commit SHA.")
            if not isinstance(path, str) or not re.fullmatch(r"skills/[a-z0-9-]+/SKILL\.md", path):
                raise Error("External skill source requires a canonical entrypoint path.")
            expected_id = repository + ":" + path.split("/")[1]
            if source.get("source_id") != expected_id or expected_id in source_ids:
                raise Error("External source IDs must match their paths and be unique.")
            if source["url"] != f"https://github.com/{repository}/blob/{revision}/{path}":
                raise Error("External source URL must point to its exact commit and path.")
            if source.get("transformation") != "independent_synthesis":
                raise Error("Only independently synthesized external knowledge is supported.")
            source_ids.add(expected_id)
    if not isinstance(pack.get("checks"), list) or not pack["checks"]:
        raise Error("Reference pack requires scoped checks.")
    check_ids, cited = set(), set()
    for check in pack["checks"]:
        if not isinstance(check, dict):
            raise Error("A pack check must be an object.")
        for field in ("id", "question", "applicability"):
            if not isinstance(check.get(field), str) or not check[field].strip():
                raise Error(f"Check {field} must be nonempty text.")
        if check["id"] in check_ids:
            raise Error("Check IDs must be unique within each pack.")
        check_ids.add(check["id"])
        for field in ("valid_counterexamples", "evidence_needed", "not_a_rule"):
            _texts(check.get(field), f"Check {field}")
        references = check.get("source_ids", [])
        _texts(references, "Check source_ids", empty=True)
        if set(references) - source_ids:
            raise Error("Check references an external source absent from its pack.")
        cited.update(references)
    if source_ids - cited:
        raise Error("External source attribution must be attached to at least one check.")
    return pack


def list_packs() -> list[dict]:
    results = []
    ids = set()
    for path in sorted(_directory().glob("*.json")):
        pack = validate_pack(load_json(path))
        if pack["id"] in ids or path.stem != pack["id"]:
            raise Error("Pack IDs must be unique and match their filenames.")
        ids.add(pack["id"])
        results.append(pack)
    return results


def source_inventory() -> dict:
    packs = {pack["id"]: pack for pack in list_packs()}
    sources = []
    for path in sorted((_directory() / "sources").glob("*.json")):
        manifest = load_json(path)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("authority") != "reference":
            raise Error("Invalid external source manifest.")
        if not isinstance(manifest.get("entries"), list):
            raise Error("Source manifest entries must be a list.")
        seen, entries = set(), []
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or entry.get("disposition") not in {"knowledge", "orchestration", "excluded_tool"}:
                raise Error("Unknown source disposition.")
            skill = entry.get("skill")
            if not isinstance(skill, str) or not re.fullmatch(r"[a-z0-9-]+", skill) or skill in seen:
                raise Error("Source skill IDs must be safe and unique.")
            seen.add(skill)
            targets = entry.get("packs")
            _texts(targets, "Mapped packs", empty=entry["disposition"] != "knowledge")
            source_id = manifest["repository"] + ":" + skill
            for target in targets:
                if target not in packs:
                    raise Error(f"Source mapping references missing pack {target}.")
                if not any(source.get("source_id") == source_id and source.get("revision") == manifest["revision"]
                           for source in packs[target]["sources"]):
                    raise Error(f"Pack {target} is missing its pinned upstream attribution.")
            if entry["disposition"] != "knowledge" and (targets or not entry.get("reason")):
                raise Error("Excluded/tool routing entries require a reason and cannot claim pack coverage.")
            entries.append(dict(entry, source_id=source_id, path=f"skills/{skill}/SKILL.md",
                                url=f"https://github.com/{manifest['repository']}/blob/{manifest['revision']}/skills/{skill}/SKILL.md"))
        counts = Counter(entry["disposition"] for entry in entries)
        sources.append(dict(manifest, entries=entries, counts=dict(counts), entry_count=len(entries)))
    return {"authority": "reference", "pack_count": len(packs),
            "check_count": sum(len(pack["checks"]) for pack in packs.values()), "sources": sources}


def _matches(term: str, query: str) -> bool:
    term = term.casefold()
    if re.fullmatch(r"[a-z0-9_:+!-]+", term):
        return re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", query) is not None
    return term in query


def select_packs(query: str, limit: int = 3) -> dict:
    if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 20:
        raise Error("Pack selection requires a nonempty query and an integer limit from 1 to 20.")
    query_lower = query.casefold()
    words = set(re.findall(r"[a-z0-9_]+", query_lower)) - {"rust", "review", "code", "the", "and", "for", "with"}
    matches = []
    for pack in list_packs():
        tags = [str(tag).casefold() for tag in pack["tags"]]
        text = f"{pack['id']} {pack['title']} {pack.get('summary', '')}".casefold()
        tag_hits = [tag for tag in tags if _matches(tag, query_lower)]
        specific_hits = [tag for tag in tag_hits if tag not in {"rust", "review", "code"}]
        matched_terms = sorted(word for word in words if _matches(word, text))
        score = 6 * len(specific_hits) + len(matched_terms)
        if not score and tag_hits and query_lower.strip() in {"rust", "review", "code"}:
            score = 1
        if score:
            matches.append((score, pack["id"], pack, specific_hits, matched_terms))
    matches.sort(key=lambda row: (-row[0], row[1]))
    return {
        "authority": "reference", "query": query,
        "packs": [dict(pack, content_hash=digest(pack),
                       selection={"matched_tags": hits, "matched_terms": terms, "score": score})
                  for score, _, pack, hits, terms in matches[:limit]],
        "matched_count": len(matches), "omitted_count": max(0, len(matches) - limit),
        "note": "Reference selection only. No pack is repository policy or independently verified evidence.",
    }
