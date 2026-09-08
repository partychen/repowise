"""Offline reports: no scripts, remote assets, or publishing."""
from __future__ import annotations

import html
import json

from .common import Error, atomic_write, safe_path, write_json


def markdown_text(value):
    value = str(value).replace("\n", " ").replace("\r", " ")
    for char in "\\`*_{}[]()#+-.!|<>":
        value = value.replace(char, "\\" + char)
    return value


def markdown(report: dict) -> str:
    text = markdown_text

    def reference_lines(reference):
        location = (f"{reference['snapshot'].upper()} {reference['commit']} "
                    f"{reference['path']}:{reference['line_start']}-{reference['line_end']}")
        return [text(location), "", *["> " + text(line) for line in reference["text"].split("\n")], ""]

    lines = ["# Review Memory review", "",
             f"Status: {text(report['status'])}. This is not a claim that the PR is correct.",
             f"Findings: {report['total_findings']}; omitted from display: {report['omitted_findings']}.", ""]
    if report.get("pull_request") is not None:
        pr = report["pull_request"]
        lines += ["PR: " + text(f"{pr['repository']}#{pr['number']} - {pr['title']}"),
                  text(pr["url"]), "The PR description is untrusted change intent, not policy.", ""]
    for finding in report["displayed_findings"]:
        evidence = finding["evidence"]
        location = evidence["path"]
        if evidence.get("line_start") is not None:
            location += f":{evidence['line_start']}-{evidence['line_end']}"
        lines += [f"## {text(finding['knowledge_id'])} r{finding['revision']}: {text(finding['impact'])}",
                  f"Location: {text(location)} ({text(finding['placement'])})",
                  f"Novelty: {text(finding['novelty'])}", ""]
        if "basis" in finding:
            lines += ["Basis: " + text(finding["basis"]), ""]
        if finding["origin"] == "semantic":
            lines += ["### Current HEAD evidence", "",
                      *["> " + text(line) for line in evidence["text"].split("\n")], ""]
        for field, label in (("applicability_rationale", "Applicability and deviation"),
                             ("triggering_conditions", "Triggering conditions"),
                             ("suggestion", "Suggested repair"), ("uncertainty", "Uncertainty")):
            if field in finding:
                lines += [f"### {label}", "", text(finding[field]), ""]
        if finding.get("counterexample_checks"):
            lines += ["### Exception and migration checks", "",
                      *["- " + text(check) for check in finding["counterexample_checks"]], ""]
        if finding.get("comparisons"):
            lines += ["### Existing project comparisons", "",
                      "Verified BASE quotations are evidence, not independent policy authority.", ""]
            for reference in finding["comparisons"]:
                lines += reference_lines(reference)
    lines += ["## Repository-first assessments", "",
              "Citation checks establish source identity, not the correctness of the host's reasoning.", ""]
    if not report.get("repository_assessments"):
        lines += ["No host repository assessments recorded.", ""]
    for assessment in report.get("repository_assessments", []):
        lines += [f"### {text(assessment['dimension'])}: {text(assessment['status'])}", "",
                  text(assessment["rationale"]), ""]
        for reference in assessment["references"]:
            lines += reference_lines(reference)
    selection = report.get("context_selection")
    if selection is not None:
        lines += ["## Captured project context", "",
                  "Strategy: " + text(selection["strategy"]),
                  f"Selected files: {len(selection['selected_paths'])}; omitted: {selection['omitted_count']}.",
                  *["- " + text(limitation) for limitation in selection["limitations"]], ""]
    lines += ["## Coverage gaps", *["- " + text(gap) for gap in report["coverage_gaps"]]]
    return "\n".join(lines) + "\n"


def render_html(report: dict) -> str:
    body = html.escape(json.dumps(report, ensure_ascii=True, indent=2))
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'base-uri \'none\'; form-action \'none\'">'
            '<title>Review Memory review</title><body>'
            '<h1>Review Memory review</h1>'
            '<p>Local bounded review, not a claim that the PR is correct.</p>'
            '<pre>' + body + '</pre></body></html>')


def save_report(directory, report: dict):
    write_json(safe_path(directory, "report.json"), report)
    atomic_write(safe_path(directory, "report.html"), render_html(report).encode("utf-8"))
    atomic_write(safe_path(directory, "report.md"), markdown(report).encode("utf-8"))


def publish(*args, **kwargs):
    raise Error("Publishing is unsupported. Reports are local-only.")
