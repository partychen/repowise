import copy
import unittest
from unittest.mock import patch

from tests import test_core
from review_memory.common import Error, digest
from review_memory.packs import list_packs, select_packs, source_inventory, validate_pack


class PackTests(unittest.TestCase):
    def test_all_packs_have_scoped_checks_and_pinned_external_sources(self):
        packs = list_packs()
        self.assertEqual(17, len(packs))
        for pack in packs:
            self.assertEqual("reference", pack["authority"])
            self.assertGreaterEqual(len(pack["checks"]), 3)
            upstream = {s["source_id"] for s in pack["sources"] if s.get("kind") == "external_skill"}
            self.assertTrue(upstream, pack["id"])
            cited = {source for check in pack["checks"] for source in check.get("source_ids", [])}
            self.assertEqual(upstream, cited)
            for source in pack["sources"]:
                if source.get("kind") == "external_skill":
                    self.assertEqual("5c40d3ad785193231b7d0dbfb8e1eb447e5edd94", source["revision"])

    def test_inventory_covers_every_entrypoint_with_explicit_disposition(self):
        result = source_inventory()
        source = result["sources"][0]
        self.assertEqual(38, source["entry_count"])
        self.assertEqual({"knowledge": 23, "orchestration": 2, "excluded_tool": 13}, source["counts"])
        self.assertEqual("unverified", source["license"]["status"])
        self.assertFalse(source["license"]["vendored_source"])
        self.assertEqual(17, result["pack_count"])
        self.assertEqual(sum(len(pack["checks"]) for pack in list_packs()), result["check_count"])
        mapped = {pack for entry in source["entries"] for pack in entry["packs"]}
        self.assertEqual({pack["id"] for pack in list_packs()}, mapped)

    def test_specific_english_queries_find_relevant_knowledge(self):
        for query, expected in (
            ("E0382 ownership", "rust-ownership"),
            ("axum HTTP", "rust-domain-web"),
            ("finance money", "rust-domain-fintech"),
            ("embedded firmware", "rust-domain-embedded"),
            ("IoT MQTT", "rust-domain-iot"),
            ("ML tensor", "rust-domain-ml"),
        ):
            with self.subTest(query=query):
                selection = select_packs(query, 3)
                self.assertIn(expected, [p["id"] for p in selection["packs"]])
                self.assertTrue(all(pack["selection"]["matched_tags"] or pack["selection"]["matched_terms"]
                                    for pack in selection["packs"]))

    def test_short_tags_are_not_arbitrary_substring_matches(self):
        pack = copy.deepcopy(list_packs()[0])
        pack.update(id="rust-sample", title="Sample", summary="Sample reference.", tags=["ml", "cli", "rust"])
        with patch("review_memory.packs.list_packs", return_value=[pack]):
            self.assertEqual([], select_packs("HTML client")["packs"])
            self.assertEqual([], select_packs("Rust completely-unmatched-topic")["packs"])
            self.assertEqual(1, len(select_packs("ML")["packs"]))

    def test_unknown_provenance_and_authority_rejected(self):
        pack = copy.deepcopy(list_packs()[0])
        bad = copy.deepcopy(pack)
        bad["authority"] = "approved"
        with self.assertRaises(Error):
            validate_pack(bad)
        bad = copy.deepcopy(pack)
        bad["checks"][0]["source_ids"] = ["invented:source"]
        with self.assertRaises(Error):
            validate_pack(bad)
        bad = copy.deepcopy(pack)
        upstream = next(source for source in bad["sources"] if source.get("kind") == "external_skill")
        upstream["revision"] = "main"
        with self.assertRaises(Error):
            validate_pack(bad)

    def test_selection_hash_and_limits(self):
        packs = {pack["id"]: pack for pack in list_packs()}
        selection = select_packs("Rust", 2)
        self.assertEqual(len(packs) - 2, selection["omitted_count"])
        for selected in selection["packs"]:
            self.assertEqual(digest(packs[selected["id"]]), selected["content_hash"])
        for limit in (0, -1, True, 21):
            with self.assertRaises(Error):
                select_packs("Rust", limit)


if __name__ == "__main__":
    unittest.main()
