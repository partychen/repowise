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

    def finding_lines(finding, level=2):
        evidence = finding["evidence"]
        location = evidence["path"]
        if evidence.get("line_start") is not None:
            location += f":{evidence['line_start']}-{evidence['line_end']}"
        heading, subheading = "#" * level, "#" * (level + 1)
        result = [f"{heading} {text(finding['knowledge_id'])} r{finding['revision']}: {text(finding['impact'])}",
                  f"Location: {text(location)} ({text(finding['placement'])})",
                  f"Novelty: {text(finding['novelty'])}", ""]
        if "basis" in finding:
            result += ["Basis: " + text(finding["basis"]), ""]
        if finding["origin"] == "semantic":
            result += [f"{subheading} Current HEAD evidence", "",
                       *["> " + text(line) for line in evidence["text"].split("\n")], ""]
        for field, label in (("applicability_rationale", "Applicability and deviation"),
                             ("triggering_conditions", "Triggering conditions"),
                             ("suggestion", "Suggested repair"), ("uncertainty", "Uncertainty")):
            if field in finding:
                result += [f"{subheading} {label}", "", text(finding[field]), ""]
        if finding.get("counterexample_checks"):
            result += [f"{subheading} Exception and migration checks", "",
                       *["- " + text(check) for check in finding["counterexample_checks"]], ""]
        if finding.get("comparisons"):
            result += [f"{subheading} Existing project comparisons", "",
                       "Verified BASE quotations are evidence, not independent policy authority.", ""]
            for reference in finding["comparisons"]:
                result += reference_lines(reference)
        return result

    lines = ["# RepoWise review", "",
             f"Status: {text(report['status'])}. This is not a claim that the PR is correct.",
             f"Findings: {report['total_findings']}; omitted from display: {report['omitted_findings']}.", ""]
    if report.get("pull_request") is not None:
        pr = report["pull_request"]
        lines += ["PR: " + text(f"{pr['repository']}#{pr['number']} - {pr['title']}"),
                  text(pr["url"]), "The PR description is untrusted change intent, not policy.", ""]
    orchestration = report.get("orchestration")
    plan = report.get("review_plan")
    if plan is not None and (plan["selected_roles"] or orchestration is not None):
        lines += ["## Host review orchestration", "",
                  "Planned mode: " + text(plan["planned_mode"]),
                  *["- " + text(reason) for reason in plan["reasons"]],
                  "Plans do not prove execution; the CLI never launches models or project commands.", ""]
        if orchestration is None:
            lines += ["No host worker reports recorded.", ""]
        else:
            lines += ["Actual mode (host-reported only): " + text(orchestration["actual_mode"]), ""]
            for worker in orchestration["workers"]:
                lines += ["- " + text(f"{worker['role_id']}: {worker['status']} — {worker['rationale']}")]
            lines += ["", "## Finding groups", "",
                      f"Raw members: {report['raw_finding_count']}; accepted findings: {report['total_findings']}; "
                      f"display groups: {report['total_display_groups']}; "
                      f"groups omitted by cap: {report['omitted_display_groups']}.", ""]
            members = {member["member_id"]: member for member in orchestration["members"]}
            for group in report["displayed_groups"]:
                primary = members[group["member_ids"][0]]
                lines += ["### " + text(f"{group['action']}: {primary['impact']}"), "",
                          text(group["rationale"]), ""]
                evidence_members = {}
                for identity in group["member_ids"]:
                    member = members[identity]
                    evidence_members.setdefault(member["finding_id"], member)
                    lines += ["- " + text(f"{identity}: {member['knowledge_id']} r{member['revision']}; "
                                         f"source={member['provenance']['source']}; "
                                         f"finding={member['finding_id']}")]
                lines += [""]
                for member in evidence_members.values():
                    lines += ["<details>", "<summary>Supporting finding and evidence</summary>", "",
                              *finding_lines(member, level=4), "</details>", ""]
            rejected = [group for group in orchestration["groups"] if group["action"] == "reject"]
            if rejected:
                lines += ["Rejected semantic members remain in the audit members and response, "
                          "not the accepted/scored findings.", ""]
                for group in rejected:
                    lines += ["- " + text(f"{group['member_ids'][0]}: {group['rationale']}")]
                lines += [""]
    if orchestration is None:
        for finding in report["displayed_findings"]:
            lines += finding_lines(finding)
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
            '<title>RepoWise review</title><body>'
            '<h1>RepoWise review</h1>'
            '<p>Local bounded review, not a claim that the PR is correct.</p>'
            '<pre>' + body + '</pre></body></html>')


def save_report(directory, report: dict):
    write_json(safe_path(directory, "report.json"), report)
    atomic_write(safe_path(directory, "report.html"), render_html(report).encode("utf-8"))
    atomic_write(safe_path(directory, "report.md"), markdown(report).encode("utf-8"))


def publish(*args, **kwargs):
    raise Error("Publishing is unsupported. Reports are local-only.")
