"""Synthetic host orchestration cases; no worker, model or target code is executed."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".github" / "skills" /
                       "repowise" / "scripts"))

from repowise.common import Error, digest, load_json, write_json
from repowise.review import _finding, _validate_response
from repowise.review_agents import DIMENSIONS, ROLES, consolidate, make_plan, member_id, worker_packets
from tests import test_review as review_fixture
from tests import test_review_contract as contract_fixture


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        fixture = contract_fixture.RepositoryContractTests()
        fixture.setUp()
        self.task = fixture.task
        self.response = fixture.response()
        self.reference = fixture.reference()
        self.task["review_plan"] = make_plan(self.task, [{"path": "src/current.rs"}],
                                            roles=["logic", "security"])
        self.response["orchestration"] = {
            "version": 1, "actual_mode": "multi", "workers": [], "decisions": [],
        }
        for role in self.task["review_plan"]["selected_roles"]:
            response = {key: copy.deepcopy(value) for key, value in self.response.items()
                        if key != "orchestration"}
            response["repository_assessments"] = [
                item for item in response["repository_assessments"] if item["dimension"] in ROLES[role]["dimensions"]
            ]
            self.response["orchestration"]["workers"].append({
                "role_id": role, "task_id": self.task["task_id"], "input_hash": self.task["input_hash"],
                "status": "completed", "rationale": "Synthetic host-reported role pass.", "response": response,
            })

    def consolidate(self, response=None, static=None):
        response = self.response if response is None else response
        findings, gaps = _validate_response(response, self.task)
        result = consolidate(self.task, response, findings, static or [], _validate_response)
        return result, gaps + result["coverage_gaps"]

    def test_small_auto_single_and_explicit_single(self):
        for mode in ("auto", "single"):
            plan = make_plan(self.task, [{"path": "src/current.rs"}], mode=mode)
            self.assertEqual("single", plan["planned_mode"])
            self.assertEqual([], plan["workers"])
            self.assertEqual({"coordinator"}, set(plan["dimension_owners"].values()))
            self.assertIn("security", plan["dimensions"])

    def test_complex_path_routing_selects_subset_and_explains_thresholds(self):
        changed = [{"path": f"src/auth/file{index}.rs"} for index in range(8)]
        plan = make_plan(self.task, changed)
        self.assertEqual(["architecture", "logic", "security"], plan["selected_roles"])
        self.assertEqual(8, plan["signals"]["changed_files"])
        self.assertEqual("security", plan["dimension_owners"]["security"])
        self.assertEqual("coordinator", plan["dimension_owners"]["errors_lifecycle"])
        self.assertTrue(plan["reasons"])
        self.assertTrue(plan["limitations"])

    def test_large_captured_line_count_and_explicit_multi_route(self):
        self.task["code_context"][0]["changed_lines"] = list(range(1, 401))
        self.assertEqual("multi", make_plan(self.task, [{"path": "src/current.rs"}])["planned_mode"])
        self.task["code_context"][0]["changed_lines"] = [1]
        self.assertEqual(["architecture", "logic"],
                         make_plan(self.task, [{"path": "one.rs"}], mode="multi")["selected_roles"])

    def test_frozen_pr_title_is_only_a_bounded_routing_signal(self):
        self.task["pull_request"] = {"title": "Update authentication token handling"}
        plan = make_plan(self.task, [{"path": "one.rs"}], mode="multi")
        self.assertIn("security", plan["selected_roles"])
        self.assertTrue(plan["signals"]["pr_metadata_available"])
        self.assertEqual("single", make_plan(self.task, [{"path": "one.rs"}])["planned_mode"])

    def test_explicit_roles_are_stable_bounded_and_exclusive(self):
        plan = make_plan(self.task, [], roles=["tests", "logic"], max_workers=1)
        self.assertEqual(["logic", "tests"], plan["selected_roles"])
        self.assertEqual(1, plan["max_parallel_workers"])
        self.assertEqual([["logic"], ["tests"]], plan["dispatch_batches"])
        dimensions = [dimension for worker in plan["workers"] for dimension in worker["dimensions"]]
        self.assertEqual(len(dimensions), len(set(dimensions)))
        self.assertEqual(set(DIMENSIONS), set(plan["dimension_owners"]))
        for worker in plan["workers"]:
            self.assertEqual(plan["coordinator_knowledge"], worker["knowledge"])

    def test_invalid_selectors(self):
        for options in ({"mode": "invalid"}, {"roles": ["fake"]}, {"roles": ["logic", "logic"]},
                        {"mode": "single", "roles": ["logic"]}, {"max_workers": 0}, {"max_workers": 7},
                        {"max_workers": True}, {"roles": "logic"}):
            with self.subTest(options=options), self.assertRaises(Error):
                make_plan(self.task, [], **options)

    def test_no_semantic_knowledge_does_not_invent_workers(self):
        self.task["knowledge"] = []
        plan = make_plan(self.task, [], mode="multi")
        self.assertEqual("single", plan["planned_mode"])
        self.assertEqual([], plan["workers"])

    def test_worker_packets_reference_one_shared_immutable_task(self):
        packets = worker_packets(self.task)
        self.assertEqual({"worker-logic.json", "worker-security.json"}, set(packets))
        for packet in packets.values():
            self.assertEqual(self.task["task_id"], packet["task_id"])
            self.assertEqual(self.task["input_hash"], packet["input_hash"])
            self.assertEqual("task.json", packet["shared_task_path"])
            self.assertIn(packet["assignment"], self.task["review_plan"]["workers"])
        packets["worker-logic.json"]["assignment"]["dimensions"].clear()
        self.assertTrue(self.task["review_plan"]["workers"][0]["dimensions"])

    def test_complete_workers_are_auditable_and_do_not_need_unassigned_dimensions(self):
        result, gaps = self.consolidate()
        self.assertEqual([], gaps)
        self.assertEqual("host_reported_only", result["execution_evidence"])
        self.assertEqual(2, len(result["workers"]))
        self.assertEqual(3, len(result["members"]))
        self.assertEqual(1, len(result["groups"]))
        self.assertEqual("exact_duplicate", result["groups"][0]["action"])
        self.assertEqual({"coordinator", "worker/logic", "worker/security"},
                         {member["provenance"]["source"] for member in result["members"]})

    def test_missing_workers_and_legacy_fallback_are_not_success(self):
        response = copy.deepcopy(self.response)
        response.pop("orchestration")
        response["repository_assessments"] = [
            item for item in response["repository_assessments"] if item["dimension"] != "security"]
        result, gaps = self.consolidate(response)
        self.assertEqual("single", result["actual_mode"])
        self.assertEqual(["not_run", "not_run"], [worker["status"] for worker in result["workers"]])
        self.assertTrue(any("security" in gap for gap in gaps))
        self.assertTrue(any("Worker logic not_run" in gap for gap in gaps))

    def test_failed_worker_cannot_be_converted_to_checked_coverage(self):
        worker = self.response["orchestration"]["workers"][0]
        worker.update(status="failed", response=None, rationale="Host worker failed.")
        with self.assertRaisesRegex(Error, "manufacture checked"):
            self.consolidate()
        for item in self.response["repository_assessments"]:
            if item["dimension"] in ROLES["logic"]["dimensions"]:
                item.update(status="needs_context", references=[])
        result, gaps = self.consolidate()
        self.assertTrue(any("Worker logic failed" in gap for gap in gaps))
        self.assertEqual("failed", result["workers"][0]["status"])

    def test_single_fallback_allows_coordinator_reinspection_but_records_missing_workers(self):
        self.response["orchestration"].update(actual_mode="single", workers=[])
        result, gaps = self.consolidate()
        self.assertEqual("single", result["actual_mode"])
        self.assertTrue(any("not_run" in gap for gap in gaps))

    def test_serial_fallback_is_explicitly_not_independent_worker_execution(self):
        self.response["orchestration"]["actual_mode"] = "serial"
        result, gaps = self.consolidate()
        self.assertEqual("serial", result["actual_mode"])
        self.assertEqual(2, len(gaps))
        self.assertTrue(all("no independent subagent" in gap for gap in gaps))

    def test_needs_context_report_has_no_fabricated_checked_coverage(self):
        worker = self.response["orchestration"]["workers"][0]
        worker["status"] = "needs_context"
        with self.assertRaisesRegex(Error, "cannot claim checked"):
            self.consolidate()
        worker["response"]["findings"] = []
        for item in worker["response"]["repository_assessments"] + worker["response"]["assessments"]:
            item["status"] = "needs_context"
        for item in self.response["repository_assessments"]:
            if item["dimension"] in ROLES["logic"]["dimensions"]:
                item["status"] = "needs_context"
        _, gaps = self.consolidate()
        self.assertTrue(any("Worker logic needs_context" in gap for gap in gaps))

    def test_unknown_stale_duplicate_malformed_and_forged_worker_reports(self):
        variants = []
        for field, value in (("role_id", "unregistered"), ("role_id", []), ("task_id", "other-task"),
                             ("input_hash", "stale"), ("status", "executed"), ("rationale", ""),
                             ("response", None), ("status", "failed")):
            response = copy.deepcopy(self.response)
            response["orchestration"]["workers"][0][field] = value
            variants.append(response)
        response = copy.deepcopy(self.response)
        response["orchestration"]["workers"].append(copy.deepcopy(response["orchestration"]["workers"][0]))
        variants.append(response)
        response = copy.deepcopy(self.response)
        response["orchestration"]["workers"][0]["source"] = "static"
        variants.append(response)
        for response in variants:
            with self.subTest(response=response), self.assertRaises(Error):
                self.consolidate(response)

    def test_worker_reuses_strict_rule_evidence_model_and_execution_validation(self):
        variants = []
        for mutate in (
            lambda response: response["findings"][0]["evidence"].update(text="forged"),
            lambda response: response["findings"][0].update(verification="executed"),
            lambda response: response["findings"][0].update(knowledge_id="K-unapproved"),
            lambda response: response["findings"][0].update(origin="static"),
            lambda response: response["findings"][0].update(basis="consistency"),
            lambda response: response["model"].update(provider="other"),
            lambda response: response.update(orchestration={}),
            lambda response: response["repository_assessments"].append(
                dict(dimension="architecture", status="checked", rationale="Outside role.",
                     references=[self.reference])),
        ):
            response = copy.deepcopy(self.response)
            mutate(response["orchestration"]["workers"][0]["response"])
            variants.append(response)
        for response in variants:
            with self.subTest(response=response), self.assertRaises(Error):
                self.consolidate(response)

    def test_missing_worker_dimension_cannot_be_filled_from_nothing(self):
        self.response["orchestration"]["workers"][0]["response"]["repository_assessments"] = []
        with self.assertRaisesRegex(Error, "manufacture checked"):
            self.consolidate()

    def test_every_final_coordinator_rule_still_requires_assessment(self):
        self.response["findings"] = []
        self.response["assessments"] = []
        _, gaps = self.consolidate()
        self.assertTrue(any("Missing semantic assessment" in gap for gap in gaps))
        self.assertTrue(any("coordinator/worker rule assessment conflict" in gap for gap in gaps))

    def test_worker_rule_assignment_is_enforced_separately_from_shared_knowledge(self):
        self.task["review_plan"]["workers"][0]["knowledge"] = []
        with self.assertRaisesRegex(Error, "Unknown or stale"):
            self.consolidate()

    def test_single_seven_key_response_can_assess_security_without_claiming_workers(self):
        self.task["review_plan"] = make_plan(self.task, [], mode="single")
        self.response.pop("orchestration")
        result, gaps = self.consolidate()
        self.assertEqual([], gaps)
        self.assertEqual("single", result["actual_mode"])
        self.assertEqual([], result["workers"])

    def test_distinct_material_defects_at_same_location_survive_exact_dedup(self):
        worker = self.response["orchestration"]["workers"][0]["response"]
        worker["findings"][0]["impact"] = "A different material consequence at the same line."
        result, _ = self.consolidate()
        self.assertEqual(2, len(result["groups"]))
        self.assertEqual(2, len({member["finding_id"] for member in result["members"]}))
        self.assertEqual(3, len(result["members"]))

    def test_explicit_cross_role_merge_keeps_all_underlying_provenance(self):
        self.response["orchestration"]["workers"][0]["response"]["findings"][0]["impact"] = "Different wording."
        ids = [member_id(self.task, "worker/logic", 0), member_id(self.task, "worker/security", 0)]
        self.response["orchestration"]["decisions"] = [
            dict(action="merge", member_ids=ids, rationale="Host asserts the same root cause after comparing both paths.")
        ]
        result, _ = self.consolidate()
        merged = next(group for group in result["groups"] if group["action"] == "merge")
        self.assertEqual(sorted(ids), merged["member_ids"])
        self.assertEqual(3, len(result["members"]))
        self.assertEqual(2, len(result["groups"]))

    def test_static_member_cannot_be_rejected_and_merge_never_erases_it(self):
        static = _finding({"id": "K-static", "revision": 1},
                          {"evidence": {"path": "Cargo.toml", "text": "forbidden"},
                           "impact": "Static consequence.", "verification": "executed"}, "static")
        identity = member_id(self.task, "static", static["finding_id"])
        self.response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[identity], rationale="Try to hide static output.")
        ]
        with self.assertRaisesRegex(Error, "Static findings cannot"):
            self.consolidate(static=[static])
        self.response["orchestration"]["decisions"] = [
            dict(action="merge", member_ids=[identity, member_id(self.task, "worker/logic", 0)],
                 rationale="Same root cause asserted; retain the actual static member.")
        ]
        result, _ = self.consolidate(static=[static])
        member = next(member for member in result["members"] if member["member_id"] == identity)
        self.assertEqual("executed", member["verification"])
        self.assertEqual("static", member["provenance"]["source"])

    def test_conflicts_stay_separate_and_rejected_semantics_remain_raw(self):
        ids = [member_id(self.task, "worker/logic", 0), member_id(self.task, "worker/security", 0)]
        self.response["orchestration"]["decisions"] = [
            dict(action="conflict", member_ids=ids, rationale="Incompatible repairs require coordinator review."),
            dict(action="reject", member_ids=[member_id(self.task, "coordinator", 0)],
                 rationale="The stated triggering condition is contradicted by the other evidence."),
        ]
        result, gaps = self.consolidate()
        self.assertEqual(3, len(result["members"]))
        self.assertEqual(2, sum(group["display"] for group in result["groups"]))
        self.assertTrue(any("Unresolved consolidation conflict" in gap for gap in gaps))

    def test_rejected_workers_resolve_rule_disagreement_without_erasing_evidence(self):
        self.response["findings"] = []
        self.response["assessments"][0].update(
            status="not_applicable", rationale="The frozen exception excludes this triggering condition.")
        identities = [member_id(self.task, f"worker/{role}", 0) for role in ("logic", "security")]
        self.response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[identity],
                 rationale="The rule's captured exception excludes this finding.")
            for identity in identities
        ]
        result, gaps = self.consolidate()
        self.assertEqual([], gaps)
        self.assertEqual(set(identities), {member["member_id"] for member in result["members"]})
        self.assertTrue(all(group["action"] == "reject" and not group["display"]
                            for group in result["groups"]))
        self.assertEqual(self.response["orchestration"]["decisions"], result["decisions"])

    def test_rejection_only_resolves_the_rejected_members_disagreement(self):
        self.response["findings"] = []
        self.response["assessments"][0].update(
            status="not_applicable", rationale="The rule's exception applies to this change.")
        rejected = member_id(self.task, "worker/logic", 0)
        unresolved = member_id(self.task, "worker/security", 0)
        self.response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[rejected], rationale="This finding overlooks the exception.")
        ]
        _, gaps = self.consolidate()
        self.assertEqual([f"Unresolved coordinator/worker rule assessment conflict: {unresolved}."], gaps)

    def test_rejecting_all_findings_does_not_fill_missing_rule_coverage(self):
        self.response["findings"] = []
        self.response["assessments"] = []
        self.response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[member_id(self.task, f"worker/{role}", 0)],
                 rationale="This particular finding is not supported.")
            for role in ("logic", "security")
        ]
        _, gaps = self.consolidate()
        self.assertTrue(any("Missing semantic assessment" in gap for gap in gaps))
        self.assertFalse(any("coordinator/worker rule assessment conflict" in gap for gap in gaps))

    def test_unknown_cross_run_double_assignment_and_bad_decisions_rejected(self):
        identity = member_id(self.task, "worker/logic", 0)
        for decisions in (
            [dict(action="merge", member_ids=[identity], rationale="Not a group.")],
            [dict(action="reject", member_ids=["other-task/worker/logic/0"], rationale="Stale.")],
            [dict(action="merge", member_ids=[identity, identity], rationale="Duplicate member.")],
            [dict(action="accept", member_ids=[identity], rationale="Accepted."),
             dict(action="reject", member_ids=[identity], rationale="Reassigned.")],
            [dict(action="merge", member_ids=[identity, member_id(self.task, "worker/security", 0)], rationale="")],
            [dict(action="suppress", member_ids=[identity], rationale="Unknown action.")],
        ):
            self.response["orchestration"]["decisions"] = decisions
            with self.subTest(decisions=decisions), self.assertRaises(Error):
                self.consolidate()


class AgentIntegrationTests(unittest.TestCase):
    setUp = review_fixture.ReviewTests.setUp
    command = review_fixture.ReviewTests.command
    write = review_fixture.ReviewTests.write
    commit = review_fixture.ReviewTests.commit
    prepare = review_fixture.ReviewTests.prepare
    response = review_fixture.ReviewTests.response
    finalize = review_fixture.ReviewTests.finalize

    def extended_response(self, prepared):
        response = self.response(prepared)
        response["orchestration"] = {"version": 1, "actual_mode": "multi", "workers": [], "decisions": []}
        for assignment in prepared["task"]["review_plan"]["workers"]:
            worker_response = self.response(prepared)
            worker_response["repository_assessments"] = [
                dict(dimension=dimension, status="checked", rationale="Synthetic frozen evidence.",
                     references=[dict(snapshot="base", commit=self.base, path="source file.py",
                                      line_start=1, line_end=1, text="old = 1")])
                for dimension in assignment["dimensions"]
            ]
            response["orchestration"]["workers"].append(
                dict(role_id=assignment["role_id"], task_id=response["task_id"], input_hash=response["input_hash"],
                     status="completed", rationale="Synthetic host attestation.", response=worker_response))
            response["repository_assessments"].extend(copy.deepcopy(worker_response["repository_assessments"]))
        return response

    def test_preparation_persists_hash_bound_packets_and_completion_keeps_reports(self):
        prepared = self.prepare(review_roles=["logic", "security"], max_review_workers=1)
        task = prepared["task"]
        self.assertEqual(task["input_hash"], digest({key: value for key, value in task.items() if key != "input_hash"}))
        self.assertEqual(2, len(prepared["worker_task_paths"]))
        for path in prepared["worker_task_paths"]:
            self.assertTrue(Path(path).is_relative_to(self.memory))
            self.assertEqual(task["input_hash"], load_json(Path(path))["input_hash"])
        response = self.extended_response(prepared)
        report = self.finalize(prepared, response)
        self.assertEqual(1, report["total_findings"])
        self.assertEqual(3, report["raw_finding_count"])
        self.assertEqual(1, report["total_display_groups"])
        directory = Path(prepared["task_path"]).parent
        self.assertEqual(response, load_json(directory / "response.json"))
        self.assertEqual(report, load_json(directory / "report.json"))
        self.assertEqual(report, self.finalize(prepared, response))
        rendered = (directory / "report.md").read_text(encoding="utf-8")
        self.assertIn("host-reported only", rendered)
        self.assertIn("security: completed", rendered)
        self.assertIn("Identical validated finding content", rendered)

    def test_task_packet_and_runtime_mutation_are_rejected(self):
        prepared = self.prepare(review_roles=["logic"])
        response = self.extended_response(prepared)
        packet_path = Path(prepared["worker_task_paths"][0])
        packet = load_json(packet_path)
        mutated = copy.deepcopy(packet)
        mutated["assignment"]["dimensions"] = ["security"]
        write_json(packet_path, mutated)
        with self.assertRaisesRegex(Error, "worker task packet was modified"):
            self.finalize(prepared, response)
        write_json(packet_path, packet)
        with patch("repowise.review.runtime_info", return_value={"mutated": True}):
            with self.assertRaisesRegex(Error, "Runtime changed"):
                self.finalize(prepared, response)
        report = self.finalize(prepared, response)
        self.assertEqual(2, report["raw_finding_count"])

    def test_single_default_same_line_distinct_and_exact_repeated_findings(self):
        prepared = self.prepare()
        self.assertEqual("single", prepared["task"]["review_plan"]["planned_mode"])
        self.assertEqual([], prepared["worker_task_paths"])
        response = self.response(prepared)
        other = copy.deepcopy(response["findings"][0])
        other["impact"] = "A distinct defect at the same location."
        response["findings"] += [other, copy.deepcopy(other)]
        report = self.finalize(prepared, response)
        self.assertEqual(2, report["total_findings"])
        self.assertNotIn("orchestration", report)

    def test_group_display_cap_preserves_all_members_and_counts(self):
        self.snapshot["config"]["max_findings_per_pr"] = 1
        prepared = self.prepare(review_roles=["logic", "security"])
        response = self.extended_response(prepared)
        response["orchestration"]["workers"][0]["response"]["findings"][0]["impact"] = "Different wording."
        task = prepared["task"]
        response["orchestration"]["decisions"] = [
            dict(action="merge", member_ids=[member_id(task, "worker/logic", 0),
                                            member_id(task, "worker/security", 0)],
                 rationale="Host asserts shared cause, without deleting either evidence record."),
        ]
        report = self.finalize(prepared, response)
        self.assertEqual(3, report["raw_finding_count"])
        self.assertEqual(2, report["total_findings"])
        self.assertEqual(2, report["total_display_groups"])
        self.assertEqual(1, len(report["displayed_groups"]))
        self.assertEqual(1, report["omitted_display_groups"])
        self.assertEqual(3, len(report["orchestration"]["members"]))
        from repowise.replay import _case_metrics
        self.assertEqual(2, _case_metrics("synthetic", report, None)["unjudged"])

    def test_merged_markdown_displays_one_issue_with_all_supporting_evidence(self):
        prepared = self.prepare(review_roles=["logic", "security"])
        response = self.extended_response(prepared)
        response["orchestration"]["workers"][0]["response"]["findings"][0]["impact"] = "Different wording"
        response["orchestration"]["decisions"] = [
            dict(action="merge",
                 member_ids=[member_id(prepared["task"], source, 0)
                             for source in ("coordinator", "worker/logic", "worker/security")],
                 rationale="The same root cause and repair explain all three reports.")
        ]
        report = self.finalize(prepared, response)
        rendered = (Path(prepared["task_path"]).parent / "report.md").read_text(encoding="utf-8")
        self.assertEqual(1, report["total_display_groups"])
        self.assertEqual(2, report["total_findings"])
        self.assertEqual(1, rendered.count("### merge:"))
        self.assertEqual(2, rendered.count("<summary>Supporting finding and evidence</summary>"))
        self.assertFalse(any(line.startswith("## K") for line in rendered.splitlines()))
        self.assertIn("Different wording", rendered)
        self.assertIn("worker/logic", rendered)
        self.assertIn("worker/security", rendered)
        self.assertIn("Current HEAD evidence", rendered)

    def test_resolved_rejections_are_persisted_without_unresolved_conflict_gaps(self):
        prepared = self.prepare(review_roles=["logic", "security"])
        response = self.extended_response(prepared)
        response["findings"] = []
        response["assessments"][0].update(
            status="not_applicable", rationale="The approved exception excludes both worker findings.")
        response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[member_id(prepared["task"], f"worker/{role}", 0)],
                 rationale="This particular finding overlooks the approved exception.")
            for role in ("logic", "security")
        ]
        report = self.finalize(prepared, response)
        self.assertEqual(0, report["total_findings"])
        self.assertEqual(2, report["rejected_member_count"])
        self.assertEqual(2, len(report["orchestration"]["members"]))
        self.assertFalse(any("coordinator/worker rule assessment conflict" in gap
                             for gap in report["coverage_gaps"]))
        self.assertEqual(report, load_json(Path(prepared["task_path"]).parent / "report.json"))

    def test_multi_plan_legacy_response_has_persistent_missing_role_coverage(self):
        prepared = self.prepare(review_roles=["reliability", "security"])
        report = self.finalize(prepared, self.response(prepared))
        self.assertEqual("single", report["orchestration"]["actual_mode"])
        self.assertEqual(["not_run", "not_run"],
                         [worker["status"] for worker in report["orchestration"]["workers"]])
        self.assertTrue(any("Worker security not_run" in gap for gap in report["coverage_gaps"]))

    def test_evaluation_directory_retains_task_scoped_worker_provenance(self):
        runs_root = self.memory / ".review" / "local" / "evaluation" / "synthetic" / "runs"
        prepared = self.prepare(review_roles=["logic"], runs_root=runs_root)
        response = self.extended_response(prepared)
        response_path = self.memory / ".review" / "local" / "evaluation" / "synthetic" / "host.json"
        write_json(response_path, response)
        from repowise.review import finalize_review
        report = finalize_review(self.memory, prepared["run_id"], response_path, runs_root=runs_root)
        self.assertEqual(".review/local/evaluation/synthetic/runs", report["runs_path"])
        self.assertTrue(all(member["member_id"].startswith(prepared["task_id"] + "/")
                            for member in report["orchestration"]["members"]))

    def test_static_results_remain_visible_inside_a_host_merge(self):
        from repowise.detectors import TOOL_ID
        self.write("Cargo.toml", '[package]\nname = "boundary"\nversion = "0.1.0"\n'
                   '[dependencies]\nforbidden = "1"\n')
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        self.snapshot["knowledge"] = [review_fixture.rule("hybrid")]
        self.snapshot["detectors"] = [{"id": "D-deps-001", "revision": 1, "tool_id": TOOL_ID,
                                      "parameters": {"from_package": "boundary", "to_package": "forbidden"}}]
        prepared = self.prepare(review_roles=["logic"])
        response = self.extended_response(prepared)
        static_id = prepared["task"]["static_findings"][0]["finding_id"]
        self.assertEqual(static_id, prepared["report"]["findings"][0]["finding_id"])
        shared_task = load_json(Path(prepared["task_path"]))
        self.assertEqual(prepared["task"]["static_findings"], shared_task["static_findings"])
        response["orchestration"]["decisions"] = [
            dict(action="merge",
                 member_ids=[member_id(prepared["task"], "static", static_id),
                             member_id(prepared["task"], "worker/logic", 0)],
                 rationale="Synthetic grouping exercise; preserve static evidence and execution provenance.")]
        report = self.finalize(prepared, response)
        self.assertTrue(any(finding["origin"] == "static" for finding in report["displayed_findings"]))
        member = next(item for item in report["orchestration"]["members"] if item["origin"] == "static")
        self.assertEqual("executed", member["verification"])
        self.assertEqual(1, report["costs"]["static"]["executions"])

    def test_replay_scoring_retains_raw_findings_and_binds_worker_response(self):
        from datetime import datetime, timedelta, timezone
        from repowise.common import utcnow
        from repowise.replay import prepare_replay, score_replay
        from repowise.review import finalize_review
        for index in range(7):
            self.write(f"new{index}.py", "value = 0\n")
        self.commit()
        self.head = self.command("rev-parse", "HEAD")
        now = datetime.now(timezone.utc)
        windows = {name: {"start": (now + timedelta(days=start)).isoformat(),
                          "end": (now + timedelta(days=end)).isoformat()}
                   for name, start, end in (("train", -3, -2), ("dev", -2, -1), ("test", -1, 1))}
        dataset = {
            "schema_version": 1, "timeline": "simulated", "windows": windows,
            "events": [dict(id="case-1", type="review", available_at=utcnow(), repository="owner/repo",
                            base=self.base, head=self.head, trusted_ref=self.policy)],
        }
        dataset_path = self.memory / ".review" / "local" / "dataset.json"
        write_json(dataset_path, dataset)
        replay = prepare_replay(self.root, dataset_path, memory_root=self.memory)
        runs_root = Path(replay["runs_root"])
        run_id = replay["runs"][0]["run_id"]
        directory = runs_root / run_id
        task = load_json(directory / "task.json")
        self.assertEqual("multi", task["review_plan"]["planned_mode"])
        response = self.extended_response({"task": task})
        response["orchestration"]["workers"][0]["response"]["findings"][0]["impact"] = "Different root cause."
        response["orchestration"]["decisions"] = [
            dict(action="reject", member_ids=[member_id(task, "worker/architecture", 0)],
                 rationale="Synthetic rejection stays auditable but is not an accepted/scored finding.")]
        response_path = directory / "host.json"
        write_json(response_path, response)
        report = finalize_review(self.memory, run_id, response_path, runs_root=runs_root)
        self.assertEqual(1, report["total_findings"])
        self.assertEqual(2, report["raw_unique_finding_count"])
        self.assertEqual(1, report["rejected_member_count"])
        self.assertTrue(any(member["impact"] == "Different root cause."
                            for member in report["orchestration"]["members"]))
        labels = {"schema_version": 1, "replay_id": replay["replay_id"],
                  "adjudicator": {"identity": "synthetic-human-fixture", "kind": "human", "independent": True},
                  "cases": []}
        labels_path = self.memory / ".review" / "local" / "labels.json"
        write_json(labels_path, labels)
        score = score_replay(self.memory, replay["replay_id"], labels_path)
        self.assertEqual(1, score["by_split"]["test"]["unjudged"])
        stored_response = load_json(directory / "response.json")
        stored_response["orchestration"]["workers"][0]["rationale"] = "Tampered attestation."
        write_json(directory / "response.json", stored_response)
        with self.assertRaisesRegex(Error, "host response"):
            score_replay(self.memory, replay["replay_id"], labels_path)

    def test_cli_selectors_and_default(self):
        from repowise.cli import parser
        base = ["review", "--trusted-ref", self.policy, "--base", self.base, "--head", self.head]
        defaults = parser().parse_args(base)
        self.assertEqual("auto", defaults.review_mode)
        self.assertEqual([], defaults.review_role)
        explicit = parser().parse_args(base + ["--review-mode", "multi", "--review-role", "security",
                                              "--review-role", "logic", "--max-review-workers", "1"])
        self.assertEqual(["security", "logic"], explicit.review_role)
        self.assertEqual(1, explicit.max_review_workers)

    def test_cli_passes_selectors_to_explicit_and_pr_preparation(self):
        from types import SimpleNamespace
        from repowise.cli import _dispatch_project, parser
        storage = SimpleNamespace(storage_root=self.memory, target_root=self.root)
        for revisions, target in (
            (["--base", self.base, "--head", self.head], "repowise.review.prepare_review"),
            (["--pr", "42"], "repowise.pull_requests.prepare_pull_request_review"),
        ):
            args = parser().parse_args(
                ["review", "--repository", "owner/repo", "--trusted-ref", self.policy, *revisions,
                 "--review-mode", "multi", "--review-role", "security", "--max-review-workers", "1"])
            with patch("repowise.cli.project_config", return_value={"repository": "owner/repo"}), \
                    patch(target, return_value={"prepared": True}) as prepare:
                self.assertEqual({"prepared": True}, _dispatch_project(args, storage))
                self.assertEqual("multi", prepare.call_args.kwargs["review_mode"])
                self.assertEqual(["security"], prepare.call_args.kwargs["review_roles"])
                self.assertEqual(1, prepare.call_args.kwargs["max_review_workers"])


if __name__ == "__main__":
    unittest.main()
