import unittest

from src.tally import (
    copy_group,
    format_amount,
    parse_amount,
    parse_website_summary,
    tally_rows,
    txn_kind,
)


class TallyTests(unittest.TestCase):
    def test_parse_and_format_amount(self) -> None:
        self.assertEqual(parse_amount("1,240.50"), 1240.5)
        self.assertEqual(parse_amount("$30"), 30.0)
        self.assertEqual(parse_amount(""), 0.0)
        self.assertEqual(format_amount(1240.5), "1,240.50")

    def test_kind_and_copy_group(self) -> None:
        self.assertEqual(txn_kind("WITHDRAW"), "withdraw")
        self.assertEqual(txn_kind("DEPOSIT"), "deposit")
        self.assertEqual(copy_group("Copied"), "sent")
        self.assertEqual(copy_group("Pending"), "to_send")
        self.assertEqual(copy_group("Failed"), "failed")

    def test_tally_rows(self) -> None:
        result = tally_rows(
            [{"amount": "30"}, {"amount": "1,200.50"}, {"amount": ""}]
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["amount"], 1230.5)

    def test_parse_website_summary(self) -> None:
        summary = parse_website_summary("Record: 236\nTotal: -878.46")
        self.assertEqual(summary["records"], 236)
        self.assertEqual(summary["total"], "-878.46")


if __name__ == "__main__":
    unittest.main()
