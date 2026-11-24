import datetime
import tempfile
import unittest
from pathlib import Path

from ledger import accounts, money, reconcile, search, statement, timefmt, v2, v3
from ledger.errors import FormatError

DATA = Path(__file__).resolve().parent / "data"
RECORD = {"id": "t-1", "account": "acc-ny", "type": "debit", "amount_minor": -1230, "currency": "USD",
          "posted_at": "2023-03-04T15:00:00Z", "memo": "café", "source_rev": 1}


class Money(unittest.TestCase):
    def test_minor_units_follow_the_currency(self):
        self.assertEqual(money.to_minor("12.30", "USD"), 1230)
        self.assertEqual(money.to_minor("1200", "JPY"), 1200)
        self.assertEqual(money.to_minor("1.250", "KWD"), 1250)

    def test_more_places_than_the_currency_has_are_refused(self):
        for amount, currency in (("1.5", "JPY"), ("1.005", "USD"), ("1e3", "USD"), ("", "USD")):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                money.to_minor(amount, currency)

    def test_formatting(self):
        self.assertEqual(money.format_minor(-123456, "USD"), "-1,234.56 USD")
        self.assertEqual(money.format_minor(5000, "JPY"), "5,000 JPY")


class Times(unittest.TestCase):
    def test_an_offset_makes_an_aware_time_and_none_a_naive_one(self):
        self.assertEqual(timefmt.to_utc_z(timefmt.parse_ts("2023-03-04T10:00:00-05:00")), "2023-03-04T15:00:00Z")
        self.assertEqual(timefmt.to_utc_z(timefmt.parse_ts("2023-03-04T10:00:00Z")), "2023-03-04T10:00:00Z")
        self.assertIsNone(timefmt.parse_ts("2023-03-04T10:00:00").tzinfo)

    def test_a_naive_time_has_no_utc_time(self):
        with self.assertRaises(ValueError):
            timefmt.to_utc_z(datetime.datetime(2023, 3, 4, 10, 0, 0))

    def test_offsets(self):
        self.assertEqual(timefmt.parse_offset("+05:30").utcoffset(None), datetime.timedelta(hours=5, minutes=30))
        for bad in ("05:30", "+5:30", "+24:00", ""):
            with self.subTest(offset=bad), self.assertRaises(ValueError):
                timefmt.parse_offset(bad)


class Files(unittest.TestCase):
    def test_the_accounts_file_loads(self):
        held = accounts.load(DATA / "accounts.json")
        self.assertEqual(held["acc-ny"].currency, "USD")
        self.assertEqual(held["acc-ny"].tz.utcoffset(None), datetime.timedelta(hours=-5))

    def test_the_sample_export_reads(self):
        lines = v2.read(DATA / "export_v2.jsonl")
        self.assertEqual(len(lines), 126)
        self.assertEqual(lines[0][0], 1)

    def test_a_v3_line_is_compact_sorted_and_keeps_non_ascii(self):
        self.assertEqual(v3.dump_line(RECORD), '{"account":"acc-ny","amount_minor":-1230,"currency":"USD",'
                         '"id":"t-1","memo":"café","posted_at":"2023-03-04T15:00:00Z","source_rev":1,'
                         '"type":"debit"}\n')

    def test_a_v3_record_with_the_wrong_sign_or_a_local_time_is_refused(self):
        for change in ({"amount_minor": 1230}, {"posted_at": "2023-03-04T10:00:00-05:00"}, {"currency": None}):
            with self.subTest(change=change), self.assertRaises(FormatError):
                v3.validate(dict(RECORD, **change))

    def test_v3_files_must_be_in_order(self):
        later = dict(RECORD, id="t-0", posted_at="2023-03-05T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FormatError):
            v3.write(Path(tmp) / "out.jsonl", [later, RECORD])


class Tools(unittest.TestCase):
    def setUp(self):
        self.records = [RECORD, dict(RECORD, id="t-2", type="credit", amount_minor=5000, memo="Wholesale order",
                                     posted_at="2023-03-05T09:00:00Z")]

    def test_find_matches_every_filter(self):
        self.assertEqual([r["id"] for r in search.find(self.records, text="wholesale")], ["t-2"])
        self.assertEqual([r["id"] for r in search.find(self.records, until="2023-03-04T23:59:59Z")], ["t-1"])
        self.assertEqual(search.find(self.records, account="acc-ber"), [])

    def test_a_statement_runs_the_balance(self):
        lines = statement.lines(self.records, "acc-ny", "USD")
        self.assertEqual(lines[-1], "Closing balance: 37.70 USD")

    def test_reconcile_names_each_balance_that_differs(self):
        self.assertEqual(reconcile.differences(self.records, {"acc-ny": {"USD": 3770}}), [])
        self.assertEqual(reconcile.differences(self.records, {"acc-ny": {"USD": 3700}}),
                         [("acc-ny", "USD", 3770, 3700)])


if __name__ == "__main__":
    unittest.main()
