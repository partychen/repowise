import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_core import knowledge
from review_memory.cli import main
from review_memory.common import Error
from review_memory.packs import list_packs, select_packs


class CliTests(unittest.TestCase):
    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_init_and_doctor(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["--root", directory]
            code, out, _ = self.call([*args, "init", "--repository", "Acme/Project"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["repository"], "acme/project")
            code, out, _ = self.call([*args, "doctor"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["initialized"])

    def test_partial_result_not_success_exit(self):
        with patch("review_memory.cli.dispatch", return_value={"status": "partial", "complete": False}):
            self.assertEqual(self.call(["doctor"])[0], 2)

    def test_expected_error_is_json(self):
        with patch("review_memory.cli.dispatch", side_effect=Error("Invalid input")):
            code, out, err = self.call(["doctor"])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertEqual(json.loads(err)["status"], "error")

    def test_pack_reference_authority_and_budget(self):
        packs = list_packs()
        self.assertGreaterEqual(len(packs), 5)
        self.assertTrue(all(pack["authority"] == "reference" for pack in packs))
        result = select_packs("Rust ownership async unsafe errors performance", limit=1)
        self.assertEqual(len(result["packs"]), 1)
        self.assertEqual(result["omitted_count"], result["matched_count"] - 1)
        with self.assertRaises(Error):
            select_packs("Rust", 0)


if __name__ == "__main__":
    unittest.main()
