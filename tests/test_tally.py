import unittest

from src.tally import (
    copy_group,
    format_amount,
    parse_amount,
    estimated_pages,
    pager_bounds,
    pager_last_from_hrefs,
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

    def test_pager_bounds_reads_last_page(self) -> None:
        current, last = pager_bounds(["Prev", "1", "2", "...", "33", "Next"])
        self.assertEqual(current, 1)
        self.assertEqual(last, 33)

    def test_estimated_pages(self) -> None:
        self.assertEqual(estimated_pages(273, 8), 35)
        self.assertEqual(estimated_pages(0, 8), 0)

    def test_simple_pagination_hrefs(self) -> None:
        last = pager_last_from_hrefs(["#page-2", "#page-28"], current=1)
        self.assertEqual(last, 28)


if __name__ == "__main__":
    unittest.main()
