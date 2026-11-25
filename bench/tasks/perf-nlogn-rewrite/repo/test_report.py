import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import make_sales
import reports

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample"


class Report(unittest.TestCase):
    def test_the_sample_report_is_unchanged(self):
        got = reports.render(SAMPLE / "sales.csv", SAMPLE / "regions.csv")
        self.assertEqual(got, (SAMPLE / "report.txt").read_text(encoding="utf-8"))

    def test_the_command_line_prints_the_same_report(self):
        res = subprocess.run([sys.executable, "-m", "reports", str(SAMPLE / "sales.csv"), str(SAMPLE / "regions.csv")],
                             cwd=HERE, capture_output=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.decode("utf-8"), (SAMPLE / "report.txt").read_text(encoding="utf-8"))

    def test_a_bad_header_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sales = Path(tmp) / "sales.csv"
            sales.write_text("id,date\n", encoding="utf-8")
            with self.assertRaises(reports.ReportError):
                reports.render(sales, SAMPLE / "regions.csv")

    def test_a_region_listed_twice_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            regions = Path(tmp) / "regions.csv"
            regions.write_text("code,name\nN,North\nN,Nord\n", encoding="utf-8")
            with self.assertRaises(reports.ReportError):
                reports.render(SAMPLE / "sales.csv", regions)

    def test_a_missing_file_exits_1_with_a_message(self):
        res = subprocess.run([sys.executable, "-m", "reports", "no-such.csv", str(SAMPLE / "regions.csv")],
                             cwd=HERE, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 1)
        self.assertIn("no-such.csv", res.stderr)

    def test_the_standings_export_lists_every_customer_in_report_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "standings.csv"
            res = subprocess.run([sys.executable, "-m", "reports", str(SAMPLE / "sales.csv"),
                                  str(SAMPLE / "regions.csv"), "--out", str(Path(tmp) / "report.txt"),
                                  "--standings", str(out)], cwd=HERE, capture_output=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
            exported = out.read_text(encoding="utf-8").splitlines()
        report = (SAMPLE / "report.txt").read_text(encoding="utf-8").split("== Customer standings ==\n")[1]
        self.assertEqual(exported[0], "rank,customer,total,sales,regions")
        self.assertEqual([line.split(",")[1] for line in exported[1:]],
                         [re.match(r"\s*\d+  (\S.*?)  ", line).group(1) for line in report.splitlines()])

    def test_the_sample_totals_agree(self):
        res = subprocess.run([sys.executable, "-m", "reports", str(SAMPLE / "sales.csv"), str(SAMPLE / "regions.csv"),
                              "--verify"], cwd=HERE, capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_200000_rows_take_under_10_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            sales = Path(tmp) / "sales.csv"
            make_sales.write(sales, 200_000, seed=7)
            res = subprocess.run([sys.executable, "-m", "reports", str(sales), str(SAMPLE / "regions.csv"),
                                  "--out", str(Path(tmp) / "report.txt")], cwd=HERE, capture_output=True, timeout=10)
            self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
