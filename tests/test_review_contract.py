"""Synthetic protocol cases, not evidence of real model review quality."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "skills" /
                       "repowise" / "scripts"))

from repowise.common import Error
from repowise.review import _validate_response
from repowise.review_contract import (
    BASELINE_DIMENSIONS, REPOSITORY_DIMENSIONS, validate_comparisons,
    validate_reference, validate_repository_assessments,
)


class RepositoryContractTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            "task_id": "synthetic-review", "input_hash": "synthetic-bound-input",
            "base_sha": "a" * 40, "head_sha": "b" * 40, "model_provider": "host",
            "knowledge": [{"id": "K-synthetic", "revision": 1,
                           "applicability": {"paths": ["src/**"], "exclusions": []}}],
            "code_context": [
                {"path": "src/current.rs", "old_path": "src/previous.rs", "status": "R",
                 "before": "pub fn previous() {}\r\n", "after": "pub fn current() {}\n",
                 "changed_lines": [1]},
                {"path": "src/added.rs", "old_path": "src/added.rs", "status": "A",
                 "before": None, "after": "pub fn newly_added() {}\n", "changed_lines": [1]},
                {"path": "src/omitted.rs", "old_path": "src/omitted.rs", "status": "context",
                 "before": None, "after": None, "changed_lines": []},
            ],
        }

    def reference(self, snapshot="base"):
        return {
            "snapshot": snapshot, "commit": self.task[f"{snapshot}_sha"],
            "path": "src/previous.rs" if snapshot == "base" else "src/current.rs",
            "line_start": 1, "line_end": 1,
            "text": "pub fn previous() {}" if snapshot == "base" else "pub fn current() {}",
        }

    def assessment(self, dimension="architecture", status="checked"):
        return {
            "dimension": dimension, "status": status, "references": [self.reference()],
            "rationale": "Synthetic schema exercise, not an architectural or model-quality judgment.",
        }

    def response(self):
        return {
            "schema_version": 2, "task_id": self.task["task_id"], "input_hash": self.task["input_hash"],
            "model": {"provider": "host", "model": "synthetic", "prompt_version": "synthetic-v2"},
            "repository_assessments": [self.assessment(dimension) for dimension in REPOSITORY_DIMENSIONS],
            "assessments": [{"knowledge_id": "K-synthetic", "revision": 1, "status": "checked",
                             "rationale": "Synthetic contract-only assessment."}],
            "findings": [{
                "knowledge_id": "K-synthetic", "revision": 1, "basis": "behavior", "comparisons": [],
                "evidence": {"path": "src/current.rs", "line_start": 1, "line_end": 1,
                             "text": "pub fn current() {}"},
                "applicability_rationale": "Synthetic fixture scope.",
                "counterexample_checks": ["Synthetic comparison of the available before/after text."],
                "impact": "Synthetic impact, not an actual defect.",
                "triggering_conditions": "Synthetic fixture only.",
                "suggestion": "Synthetic minimal-change placeholder.",
                "uncertainty": "No project command or real model was run.",
                "verification": "not_run",
            }],
        }

    def test_complete_v2_response_and_direct_behavior_without_precedent(self):
        findings, gaps = _validate_response(self.response(), self.task)
        self.assertEqual([], gaps)
        self.assertEqual(1, len(findings))
        self.assertEqual("behavior", findings[0]["basis"])
        self.assertEqual([], findings[0]["comparisons"])

    def test_consistency_finding_requires_pinned_base_comparison(self):
        response = self.response()
        finding = response["findings"][0]
        finding["basis"] = "consistency"
        with self.assertRaisesRegex(Error, "at least one BASE"):
            _validate_response(response, self.task)
        finding["comparisons"] = [self.reference("head")]
        with self.assertRaisesRegex(Error, "BASE evidence"):
            _validate_response(response, self.task)
        finding["comparisons"] = [self.reference()]
        findings, gaps = _validate_response(response, self.task)
        self.assertEqual([], gaps)
        self.assertEqual([self.reference()], findings[0]["comparisons"])

    def test_unsupported_response_does_not_bypass_repository_first_contract(self):
        response = self.response()
        response["schema_version"] = 1
        with self.assertRaisesRegex(Error, "schema version"):
            _validate_response(response, self.task)
        response["schema_version"] = 2
        del response["repository_assessments"]
        with self.assertRaisesRegex(Error, "repository_assessments"):
            _validate_response(response, self.task)

    def test_missing_dimensions_and_needed_context_are_not_success(self):
        response = self.response()
        response["findings"] = []
        assessment = self.assessment("architecture", "needs_context")
        assessment.update(references=[], rationale="Caller ownership was not captured.")
        response["repository_assessments"] = [assessment]
        findings, gaps = _validate_response(response, self.task)
        self.assertEqual([], findings)
        self.assertEqual(len(REPOSITORY_DIMENSIONS), len(gaps))
        self.assertIn("Repository architecture: Caller ownership was not captured.", gaps)
        self.assertIn("Missing repository assessment: idioms", gaps)

    def test_checked_dimensions_need_real_references_and_some_need_base(self):
        assessment = self.assessment()
        assessment["references"] = []
        with self.assertRaisesRegex(Error, "require frozen source"):
            validate_repository_assessments([assessment], self.task)
        assessment["references"] = [self.reference("head")]
        for dimension in BASELINE_DIMENSIONS:
            assessment["dimension"] = dimension
            with self.subTest(dimension=dimension), self.assertRaisesRegex(Error, "requires BASE"):
                validate_repository_assessments([assessment], self.task)
        assessment["dimension"] = "contracts_types"
        gaps = validate_repository_assessments([assessment], self.task)
        self.assertNotIn("Missing repository assessment: contracts_types", gaps)

    def test_not_applicable_requires_reason_without_inventing_a_precedent(self):
        assessment = self.assessment("concurrency_performance", "not_applicable")
        assessment["references"] = []
        gaps = validate_repository_assessments([assessment], self.task)
        self.assertNotIn("Missing repository assessment: concurrency_performance", gaps)
        assessment["rationale"] = " "
        with self.assertRaisesRegex(Error, "rationale"):
            validate_repository_assessments([assessment], self.task)

    def test_malformed_duplicate_and_unknown_dimensions_are_rejected(self):
        variants = [None, {}, [self.assessment(), self.assessment()]]
        for key, value in (("dimension", "personal_preference"), ("dimension", []),
                           ("status", "approved"), ("status", True), ("references", {}),
                           ("rationale", "")):
            assessment = self.assessment()
            assessment[key] = value
            variants.append([assessment])
        assessment = self.assessment()
        assessment["approved"] = True
        variants.append([assessment])
        for assessments in variants:
            with self.subTest(assessments=assessments), self.assertRaises(Error):
                validate_repository_assessments(assessments, self.task)

    def test_static_acknowledgement_cannot_claim_repository_inspection(self):
        self.task["knowledge"] = []
        self.assertEqual([], validate_repository_assessments([], self.task))
        with self.assertRaisesRegex(Error, "Static-only"):
            validate_repository_assessments([self.assessment()], self.task)

    def test_snapshot_path_commit_range_and_exact_text_are_checked(self):
        variants = []
        for key, value in (
            ("snapshot", "policy"), ("snapshot", True), ("commit", self.task["head_sha"]),
            ("path", "src/current.rs"), ("path", "src/omitted.rs"), ("path", "src/added.rs"),
            ("path", "../outside"), ("path", "src\\previous.rs"), ("path", "C:/outside"),
            ("line_start", 0), ("line_end", 2), ("line_start", True), ("line_end", "1"),
            ("text", "invented"), ("text", ""), ("text", "pub fn previous() {}\n"),
        ):
            reference = self.reference()
            reference[key] = value
            variants.append(reference)
        reference = self.reference()
        reference["verified"] = True
        variants.append(reference)
        for reference in variants:
            with self.subTest(reference=reference), self.assertRaises(Error):
                validate_reference(reference, self.task)
        validate_reference(self.reference(), self.task)
        validate_reference(self.reference("head"), self.task)

    def test_bad_references_are_rejected_even_on_a_needs_context_assessment(self):
        assessment = self.assessment(status="needs_context")
        assessment["references"][0]["text"] = "fabricated quotation"
        with self.assertRaisesRegex(Error, "exact frozen source"):
            validate_repository_assessments([assessment], self.task)

    def test_comparison_cannot_upgrade_an_unapproved_knowledge_id(self):
        response = self.response()
        response["findings"][0].update(basis="consistency", comparisons=[self.reference()],
                                       knowledge_id="K-invented-convention")
        with self.assertRaisesRegex(Error, "Unknown or stale"):
            _validate_response(response, self.task)

    def test_invalid_basis_and_comparison_shape_are_rejected(self):
        for changes in ({"basis": "taste"}, {"basis": []}, {"comparisons": {}},
                        {"comparisons": [self.reference("head")]}):
            candidate = copy.deepcopy(self.response()["findings"][0])
            candidate.update(changes)
            with self.subTest(changes=changes), self.assertRaises(Error):
                validate_comparisons(candidate, self.task)


if __name__ == "__main__":
    unittest.main()
