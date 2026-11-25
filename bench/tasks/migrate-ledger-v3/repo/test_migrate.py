import json
import tempfile
import unittest
from pathlib import Path

from ledger import reconcile, v3
from ledger.errors import MigrationError
from ledger.migrate_v3 import migrate

DATA = Path(__file__).resolve().parent / "data"


def line(**fields):
    record = {"id": "t-1", "rev": 1, "account": "acc-ny", "kind": "credit", "amount": "1.00", "currency": "USD",
              "ts": "2023-01-02T09:00:00Z", "memo": "", "exported_at": "2023-01-03T01:00:00Z"}
    record.update(fields)
    return json.dumps(record) + "\n"


class Migrate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dst = Path(self.tmp.name) / "ledger_v3.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, *lines):
        src = Path(self.tmp.name) / "export_v2.jsonl"
        src.write_text("".join(lines), encoding="utf-8")
        return src

    def test_the_sample_export_migrates_to_the_expected_file(self):
        count = migrate(DATA / "export_v2.jsonl", DATA / "accounts.json", self.dst)
        expected = (DATA / "expected_v3.jsonl").read_text(encoding="utf-8")
        self.assertEqual(self.dst.read_text(encoding="utf-8"), expected)
        self.assertEqual(count, len(expected.splitlines()))

    def test_the_migrated_sample_matches_the_control_totals(self):
        migrate(DATA / "export_v2.jsonl", DATA / "accounts.json", self.dst)
        controls = reconcile.load_controls(DATA / "control_totals.json")
        self.assertEqual(reconcile.differences(v3.read(self.dst), controls), [])

    def test_an_unknown_kind_stops_the_migration_and_writes_nothing(self):
        src = self.export(line(), line(id="t-2", kind="transfer"))
        with self.assertRaises(MigrationError):
            migrate(src, DATA / "accounts.json", self.dst)
        self.assertFalse(self.dst.exists())

    def test_an_unknown_account_stops_the_migration(self):
        src = self.export(line(account="acc-nowhere"))
        with self.assertRaises(MigrationError):
            migrate(src, DATA / "accounts.json", self.dst)

    def test_too_many_decimal_places_stop_the_migration(self):
        src = self.export(line(amount="1.005"))
        with self.assertRaises(MigrationError):
            migrate(src, DATA / "accounts.json", self.dst)

    def test_an_empty_export_writes_an_empty_file(self):
        self.assertEqual(migrate(self.export(), DATA / "accounts.json", self.dst), 0)
        self.assertEqual(self.dst.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
