"""Offline reports: no scripts, remote assets, or publishing."""
from __future__ import annotations

import html
import json

from .common import Error, atomic_write, safe_path, write_json


def markdown(report: dict) -> str:
    # Escape markup and strip line breaks from untrusted prose.
    def text(value):
        value = str(value).replace("\n", " ").replace("\r", " ")
        for char in "\\`*_{}[]()#+-.!|<>":
            value = value.replace(char, "\\" + char)
        return value

    lines = ["# Review Memory review", "",
             f"Status: {text(report['status'])}. This is not a claim that the PR is correct.",
             f"Findings: {report['total_findings']}; omitted from display: {report['omitted_findings']}.", ""]
    for finding in report["displayed_findings"]:
        lines += [f"## {text(finding['knowledge_id'])}: {text(finding['impact'])}",
                  f"Location: {text(finding['evidence']['path'])} ({text(finding['placement'])})",
                  f"Novelty: {text(finding['novelty'])}", text(finding["suggestion"]), ""]
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
