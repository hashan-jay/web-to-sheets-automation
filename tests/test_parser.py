import unittest

from src.parser import parse_transactions_from_html, parse_transactions_from_text

SAMPLE = """
WITHDRAW
#17113600239COPY
Username: A51088178
Name: [JKFCKSPNAU] Timothy David Evans
Mobile: 61******610
Amount: 530.27
Bank: PIPN
BankAccountName: Timothy David Evans
BankAccountNumber: 100512365
BankBSB: 815000
PayID: 61414769587
BankLock: 1
Method: Manual
CREATED
2026-08-29 10:44
PROCESSED
2026-08-29 10:44
"""


class ParserTests(unittest.TestCase):
    def test_parse_withdraw_card(self) -> None:
        rows = parse_transactions_from_text(SAMPLE)
        self.assertEqual(len(rows), 1)
        txn = rows[0]
        self.assertEqual(txn.transaction_id, "17113600239")
        self.assertEqual(txn.username, "A51088178")
        self.assertEqual(txn.amount, "530.27")
        self.assertEqual(txn.bsb, "815000")
        self.assertEqual(txn.pay_id, "61414769587")
        self.assertEqual(txn.status, "WITHDRAW")
        self.assertEqual(txn.created, "2026-08-29 10:44")

    def test_parse_transactions_list_html(self) -> None:
        html = """
        <div id="transactions-list"><table><tbody>
        <tr class="PROCESSING " data-id="17113786958">
          <td>
            <div class="type WITHDRAW">WITHDRAW</div>
            <div class="copy">#17113786958<span>COPY</span><input type="text" class="hidden" value="17113786958"></div>
            <div class="copy">Username: A39759077<input type="text" class="hidden" value="A39759077"></div>
            <div class="copy">amount: 50<input type="text" class="hidden" value="50"></div>
            <div class="copy">bankAccountName: Brock bromage<input type="text" class="hidden" value="Brock bromage"></div>
            <div class="copy">bankBSB: 112879<input type="text" class="hidden" value="112879"></div>
            <div class="copy">payID: 0410793998<input type="text" class="hidden" value="0410793998"></div>
            <span class="name-blacklist">FUCKFUCKVIPC</span>
            <span class="name-blacklist">NETLOSSB</span>
          </td>
          <td>
            <p><b>CREATED</b><br>2026-08-29 12:13</p>
            <p><b>PROCESSED</b><br>2026-08-29 12:14</p>
          </td>
        </tr>
        <tr class="PROCESSING " data-id="17113600239">
          <td>
            <div class="type WITHDRAW">WITHDRAW</div>
            <div class="copy">Username: A51088178<input type="text" class="hidden" value="A51088178"></div>
            <div class="copy">amount: 530.27<input type="text" class="hidden" value="530.27"></div>
            <div class="copy">bankAccountName: Timothy david evans<input type="text" class="hidden" value="Timothy david evans"></div>
          </td>
        </tr>
        </tbody></table></div>
        """
        rows = parse_transactions_from_html(html)
        self.assertEqual([row.transaction_id for row in rows], ["17113786958", "17113600239"])
        first = rows[0]
        self.assertEqual(first.username, "A39759077")
        self.assertEqual(first.amount, "50")
        self.assertEqual(first.bsb, "112879")
        self.assertEqual(first.pay_id, "0410793998")
        self.assertEqual(first.status, "WITHDRAW")
        self.assertEqual(first.created, "2026-08-29 12:13")
        self.assertEqual(first.brand, "FUCKFUCKVIPC")
        self.assertEqual(rows[1].amount, "530.27")

    def test_parse_brand_from_profile_pills(self) -> None:
        html = """
        <div id="transactions-list"><table><tbody>
        <tr class="PROCESSING " data-id="17113786958">
          <td>
            <a class="link profile"><i class="fa fa-user"></i>[JKFCKSPNAU] Michael Hill<span class="badge">FUCKFUCKVIPC</span><span class="label">NETLOSSB</span></a>
            <div class="type DEPOSIT">DEPOSIT</div>
            <div class="copy">Username: A1<input type="text" class="hidden" value="A1"></div>
            <div class="copy">amount: 10<input type="text" class="hidden" value="10"></div>
          </td>
        </tr>
        </tbody></table></div>
        """
        rows = parse_transactions_from_html(html)
        self.assertEqual(rows[0].brand, "FUCKFUCKVIPC")

    def test_parse_brand_from_pending_and_cuntwin_tags(self) -> None:
        html = """
        <div id="transactions-list"><table><tbody>
        <tr class="PROCESSING " data-id="17120001001">
          <td>
            <a class="link profile"><i class="fa fa-user"></i>Pisey Pich<span class="name-blacklist">CUNTWIN</span><span class="name-blacklist">PENDING</span></a>
            <div class="type STAFF DEPOSIT">STAFF DEPOSIT</div>
            <div class="copy">Username: A9<input type="text" class="hidden" value="A9"></div>
            <div class="copy">amount: 50<input type="text" class="hidden" value="50"></div>
          </td>
        </tr>
        <tr class="PROCESSING " data-id="17120001002">
          <td>
            <a class="link profile">Other Player<span class="badge">PENDING</span><span class="label">CUNTWIN</span><span class="label">NETLOSSA</span></a>
            <div class="type STAFF DEPOSIT">STAFF DEPOSIT</div>
            <div class="copy">Username: A10<input type="text" class="hidden" value="A10"></div>
            <div class="copy">amount: 25<input type="text" class="hidden" value="25"></div>
          </td>
        </tr>
        </tbody></table></div>
        """
        rows = parse_transactions_from_html(html)
        self.assertEqual(rows[0].brand, "CUNTWIN")
        self.assertEqual(rows[0].tags, ["CUNTWIN"])
        self.assertEqual(rows[1].brand, "CUNTWIN")
        self.assertIn("CUNTWIN", rows[1].tags)


if __name__ == "__main__":
    unittest.main()

