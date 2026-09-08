"""Allowlisted, non-executing detectors over commit-qualified source text."""
from __future__ import annotations

import tomllib

TOOL_ID = "rust.forbidden-dependency.v1"


def _dependencies(document):
    for kind in ("dependencies", "dev-dependencies", "build-dependencies"):
        table = document.get(kind, {})
        if isinstance(table, dict):
            for alias, value in table.items():
                yield kind, alias, value
    for target, tables in document.get("target", {}).items():
        if isinstance(tables, dict):
            for kind, alias, value in _dependencies({k: v for k, v in tables.items() if k != "target"}):
                yield f"target.{target}.{kind}", alias, value


def dependency_edges(text: str, workspace_text: str | None = None) -> dict:
    """Parse real TOML, including aliases and inherited workspace dependencies."""
    try:
        document = tomllib.loads(text)
        workspace = tomllib.loads(workspace_text) if workspace_text is not None else None
        package = document.get("package", {}).get("name")
        if package is not None and not isinstance(package, str):
            return {"package": None, "edges": [], "gaps": ["Package name is not a literal string."]}
        inherited = workspace.get("workspace", {}).get("dependencies", {}) if workspace else {}
        if document.get("package", {}).get("workspace") is not None:
            inherited = {}
        edges, gaps = [], []
        for section, alias, value in _dependencies(document):
            if not isinstance(value, (str, dict)):
                gaps.append(f"Unsupported dependency value: {section}.{alias}")
                continue
            effective = value
            if isinstance(value, dict) and value.get("workspace") is True:
                if "workspace" in document.get("package", {}):
                    gaps.append(f"Explicit package.workspace unresolved: {section}.{alias}")
                    continue
                effective = inherited.get(alias)
                if not isinstance(effective, (str, dict)):
                    gaps.append(f"Workspace dependency unresolved: {section}.{alias}")
                    continue
            target = effective.get("package", alias) if isinstance(effective, dict) else alias
            if not isinstance(target, str):
                gaps.append(f"Nonliteral dependency package: {section}.{alias}")
                continue
            edges.append({"section": section, "alias": alias, "package": target})
        return {"package": package, "edges": edges, "gaps": gaps}
    except (tomllib.TOMLDecodeError, AttributeError, TypeError) as exc:
        return {"package": None, "edges": [], "gaps": [f"Cannot parse Cargo manifest: {exc}"]}


def forbidden_dependency(detector: dict, contexts: list[dict]) -> dict:
    """Compare actual dependency edges; uncertain baseline is never called new."""
    if detector.get("tool_id") != TOOL_ID:
        return {"findings": [], "coverage_gaps": ["Unsupported detector tool; no code executed."]}
    parameters = detector.get("parameters", {})
    source, target = parameters.get("from_package"), parameters.get("to_package")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        return {"findings": [], "coverage_gaps": ["Detector requires literal from_package/to_package."]}
    manifests = {c["path"]: c for c in contexts if c["path"].split("/")[-1] == "Cargo.toml"}
    gaps, findings = [], []
    found_source = False

    def parse(context, side):
        text = context.get(side)
        if text is None:
            return None
        # The nearest ancestor workspace is the conservative static resolution.
        parts = context["path"].split("/")[:-1]
        candidates = ["/".join(parts[:n] + ["Cargo.toml"]) for n in range(len(parts), -1, -1)]
        workspace_text = None
        for candidate in candidates:
            candidate_text = manifests.get(candidate, {}).get(side)
            if candidate_text is None:
                continue
            try:
                candidate_document = tomllib.loads(candidate_text)
                if "workspace" in candidate_document:
                    workspace_text = candidate_text
                    break
            except tomllib.TOMLDecodeError:
                continue
        return dependency_edges(text, workspace_text)

    for path, context in manifests.items():
        after, before = parse(context, "after"), parse(context, "before")
        for side, result in (("head", after), ("base", before)):
            if result:
                gaps.extend(f"{path} ({side}): {g}" for g in result["gaps"])
        if not after or after["package"] != source:
            continue
        found_source = True
        baseline_known = (before is not None and not before["gaps"]) or context.get("status") == "A"
        if not baseline_known:
            gaps.append(f"{path}: baseline dependency graph is incomplete; novelty is uncertain.")
        old_edges = {e["package"] for e in (before or {}).get("edges", [])
                     if (before or {}).get("package") == source}
        for edge in after["edges"]:
            if edge["package"] != target:
                continue
            novelty = ("pre_existing" if target in old_edges else
                       "introduced" if baseline_known else "unknown")
            findings.append({
                "evidence": {"path": path, "line_start": None, "line_end": None,
                             "text": f"{source} -> {target}", "parsed_dependency": edge},
                "novelty": novelty, "placement": "summary",
                "applicability_rationale": f"Parsed package.name is {source}; dependency package is {target}.",
                "counterexample_checks": [
                    "Parsed TOML rather than comments or string searches.",
                    "Resolved dependency aliases and available workspace declarations.",
                    "Compared the corresponding base manifest."],
                "impact": f"Forbidden dependency edge {source} -> {target} is present.",
                "triggering_conditions": f"Dependency declared in {edge['section']}; feature/cfg activation is not evaluated.",
                "suggestion": "Remove the forbidden edge or obtain an approved rule revision.",
                "uncertainty": "Static declaration only; Cargo resolution and compilation were not run.",
                "verification": "inspected",
            })
    if not found_source:
        gaps.append(f"Package {source} was not resolved in the bounded manifest context.")
    return {"findings": findings, "coverage_gaps": gaps}
