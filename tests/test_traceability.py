"""Tests for the requirement-traceability count. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import traceability  # noqa: E402

TABLE = """\
| ID | Requirement | Implementation | Source | Verification | Evidence | Status |
| :- | :- | :- | :- | :- | :- | :- |
| S2.1 | a | b | c | d | e | PASS |
| S2.2 | a | b | c | d | e | **PASS** |
| S3.1 | a | b | c | d | e | PARTIAL |
"""


class Traceability(unittest.TestCase):
    def test_rows_are_counted_by_their_last_cell(self):
        self.assertEqual(traceability.count(TABLE), {"PASS": 2, "PARTIAL": 1, "FAIL": 0, "BLOCKED": 0})
        self.assertEqual(traceability.summary(traceability.count(TABLE)),
                         "Summary: 2 PASS, 1 PARTIAL, 0 FAIL, 0 BLOCKED, 3 requirements.")

    def test_an_unknown_status_or_a_repeated_id_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "S3.1"):
            traceability.count(TABLE.replace("| PARTIAL |", "| Done |"))
        with self.assertRaisesRegex(ValueError, "S2.1"):
            traceability.count(TABLE.replace("| S2.2 |", "| S2.1 |"))

    def test_the_docs_summary_line_matches_its_rows(self):
        text = traceability.DOC.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.startswith("Summary: ")]
        self.assertEqual(lines, [traceability.summary(traceability.count(text))])


if __name__ == "__main__":
    unittest.main()
