import os
import sys
import unittest
from datetime import date
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Helpers as h  # noqa: E402

CATS = {"income": ["Salary", "Freelance", "Other"],
        "expense": ["Food", "Rent", "Bills", "Other"]}


class AmountTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(h.parse_amount("12.5"), D("12.50"))
        self.assertEqual(h.parse_amount(" 100 "), D("100.00"))
        self.assertEqual(h.parse_amount("0.005"), D("0.01"))  # rounds half up

    def test_rejects_non_finite_and_junk(self):
        for bad in ["NaN", "nan", "Infinity", "-Infinity", "1e999999", "abc", "", None, "1,000", "12.5.5"]:
            self.assertIsNone(h.parse_amount(bad), bad)

    def test_sign_and_zero_rules(self):
        self.assertIsNone(h.parse_amount("-5"))
        self.assertIsNone(h.parse_amount("0"))
        self.assertEqual(h.parse_amount("-5", allow_negative=True), D("-5.00"))
        self.assertEqual(h.parse_amount("0", allow_zero=True), D("0.00"))

    def test_upper_bound(self):
        self.assertEqual(h.parse_amount("999999999.99"), D("999999999.99"))
        self.assertIsNone(h.parse_amount("1000000000"))
        self.assertIsNone(h.parse_amount("-1000000000", allow_negative=True))

    def test_rate(self):
        self.assertEqual(h.parse_rate("9"), D("9.000"))
        self.assertEqual(h.parse_rate("0"), D("0.000"))
        for bad in ["-1", "101", "NaN", "x", "", None]:
            self.assertIsNone(h.parse_rate(bad), bad)


class TextAndRedirectTests(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(h.parse_date("2026-02-28"), date(2026, 2, 28))
        for bad in ["2026-02-30", "28/02/2026", "", None, "1800-01-01", "2200-01-01", "garbage"]:
            self.assertIsNone(h.parse_date(bad), bad)

    def test_clean_text(self):
        self.assertEqual(h.clean_text("  a   b\t c ", 10), "a b c")
        self.assertIsNone(h.clean_text("   ", 10))
        self.assertIsNone(h.clean_text("x" * 11, 10))
        self.assertIsNone(h.clean_text("bad\x00name", 20))

    def test_clean_note_truncates(self):
        self.assertEqual(len(h.clean_note("x" * 400)), 255)
        self.assertIsNone(h.clean_note("   "))
        self.assertEqual(h.clean_note("a\x00b"), "ab")

    def test_escape_like(self):
        self.assertEqual(h.escape_like("50%_off\\"), "50\\%\\_off\\\\")

    def test_safe_next(self):
        d = "/dashboard"
        self.assertEqual(h.safe_next("/history?page=2", d), "/history?page=2")
        for bad in ["https://evil.com", "//evil.com", "evil.com", "/\\evil.com",
                    "javascript:alert(1)", "/ok\nHeader: x", "", None, 5]:
            self.assertEqual(h.safe_next(bad, d), d, bad)


class PaginationTests(unittest.TestCase):
    def test_paginate(self):
        p = h.paginate(45, 2, 20)
        self.assertEqual((p["pages"], p["offset"], p["has_prev"], p["has_next"]), (3, 20, True, True))
        self.assertEqual(h.paginate(0, 1, 20)["pages"], 1)
        self.assertEqual(h.paginate(45, 99, 20)["page"], 3)   # clamps high
        self.assertEqual(h.paginate(45, -4, 20)["page"], 1)   # clamps low
        self.assertEqual(h.paginate(40, 1, 20)["pages"], 2)   # exact multiple

    def test_window(self):
        self.assertEqual(h.page_window(1, 1), [1])
        self.assertEqual(h.page_window(1, 3), [1, 2, 3])
        self.assertEqual(h.page_window(10, 20), [1, None, 9, 10, 11, None, 20])
        self.assertEqual(h.page_window(2, 20), [1, 2, 3, None, 20])

    def test_parse_page(self):
        self.assertEqual(h.parse_page("3"), 3)
        for bad in [None, "x", "0", "-2"]:
            self.assertEqual(h.parse_page(bad), 1)


class RecurringTests(unittest.TestCase):
    def test_add_months_clamps_and_keeps_anchor(self):
        self.assertEqual(h.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(h.add_months(date(2028, 1, 31), 1), date(2028, 2, 29))   # leap year
        self.assertEqual(h.add_months(date(2026, 2, 28), 1, anchor_day=31), date(2026, 3, 31))
        self.assertEqual(h.add_months(date(2026, 12, 15), 1), date(2027, 1, 15))  # year rollover
        self.assertEqual(h.add_months(date(2026, 1, 15), 12), date(2027, 1, 15))

    def test_monthly_does_not_drift(self):
        current = date(2026, 1, 31)
        seen = []
        for _ in range(4):
            current = h.next_occurrence(current, "monthly", 31)
            seen.append(current)
        self.assertEqual(seen, [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)])

    def test_other_frequencies(self):
        self.assertEqual(h.next_occurrence(date(2026, 1, 1), "daily"), date(2026, 1, 2))
        self.assertEqual(h.next_occurrence(date(2026, 1, 1), "weekly"), date(2026, 1, 8))
        self.assertEqual(h.next_occurrence(date(2028, 2, 29), "yearly", 29), date(2029, 2, 28))
        with self.assertRaises(ValueError):
            h.next_occurrence(date(2026, 1, 1), "hourly")

    def test_due_dates(self):
        dates, nxt = h.due_dates(date(2026, 1, 15), "monthly", 15, date(2026, 4, 20))
        self.assertEqual(dates, [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)])
        self.assertEqual(nxt, date(2026, 5, 15))
        dates, nxt = h.due_dates(date(2026, 5, 1), "monthly", 1, date(2026, 4, 20))
        self.assertEqual((dates, nxt), ([], date(2026, 5, 1)))        # not due yet
        dates, nxt = h.due_dates(date(2026, 4, 20), "monthly", 20, date(2026, 4, 20))
        self.assertEqual(dates, [date(2026, 4, 20)])                  # due today counts

    def test_due_dates_limit(self):
        dates, nxt = h.due_dates(date(2020, 1, 1), "daily", None, date(2026, 1, 1), limit=366)
        self.assertEqual(len(dates), 366)
        self.assertEqual(nxt, date(2021, 1, 1))                       # remainder left for next run

    def test_advance_to_future(self):
        self.assertEqual(h.advance_to_future(date(2026, 1, 15), "monthly", 15, date(2026, 4, 20)), date(2026, 5, 15))
        self.assertEqual(h.advance_to_future(date(2026, 6, 1), "monthly", 1, date(2026, 4, 20)), date(2026, 6, 1))


class LoanTests(unittest.TestCase):
    def test_known_emi(self):
        # 500,000 at 9% for 60 months
        self.assertEqual(h.calc_emi(500000, 9, 60), D("10379.18"))
        self.assertEqual(h.calc_emi(1200, 0, 12), D("100.00"))

    def test_schedule_ends_at_zero_and_adds_up(self):
        for principal, rate, months in [(500000, 9, 60), (10000, 12.5, 7), (1000, 0, 3), (333.33, 7.25, 11)]:
            rows = h.amortization_schedule(D(str(principal)), D(str(rate)), months)
            self.assertEqual(len(rows), months)
            self.assertEqual(rows[-1]["balance"], D("0.00"))
            self.assertEqual(sum(r["principal"] for r in rows), D(str(principal)).quantize(D("0.01")))
            for r in rows:
                self.assertEqual(r["payment"], r["interest"] + r["principal"])
                self.assertGreaterEqual(r["balance"], 0)

    def test_status_progress(self):
        start = date(2026, 1, 31)
        s = h.loan_status(12000, 12, 12, start, date(2026, 4, 30))
        self.assertEqual(s["paid"], 3)                 # 28 Feb, 31 Mar, 30 Apr
        self.assertEqual(s["next_due"], date(2026, 5, 31))
        self.assertEqual(s["remaining"], 9)
        self.assertEqual(s["balance"], s["schedule"][2]["balance"])
        self.assertEqual(s["principal_paid"] + s["balance"], D("12000"))
        self.assertEqual([r["paid"] for r in s["schedule"]][:4], [True, True, True, False])

    def test_status_edges(self):
        s = h.loan_status(1000, 10, 6, date(2026, 6, 1), date(2026, 6, 1))   # start day: nothing due yet
        self.assertEqual((s["paid"], s["balance"], s["finished"]), (0, D("1000"), False))
        s = h.loan_status(1000, 10, 6, date(2020, 1, 1), date(2026, 6, 1))   # long finished
        self.assertEqual((s["paid"], s["balance"], s["finished"], s["next_due"]), (6, D("0.00"), True, None))
        self.assertEqual(s["progress"], 100)


class GoalTests(unittest.TestCase):
    def test_progress(self):
        g = h.goal_progress(D("250"), D("1000"), date(2026, 10, 15), date(2026, 4, 15))
        self.assertEqual(g["percent"], 25.0)
        self.assertEqual(g["remaining"], D("750.00"))
        self.assertFalse(g["reached"])
        self.assertEqual(g["days_left"], 183)
        self.assertEqual(g["monthly_needed"], D("125.00"))   # 750 over 6 months

    def test_reached_and_overdue(self):
        g = h.goal_progress(D("1200"), D("1000"), None, date(2026, 4, 15))
        self.assertTrue(g["reached"])
        self.assertEqual((g["percent"], g["remaining"], g["monthly_needed"]), (100.0, D("0.00"), None))
        g = h.goal_progress(D("10"), D("1000"), date(2026, 1, 1), date(2026, 4, 15))
        self.assertTrue(g["overdue"])
        self.assertIsNone(g["monthly_needed"])

    def test_no_date_and_zero_target(self):
        self.assertIsNone(h.goal_progress(D("0"), D("500"), None, date(2026, 1, 1))["days_left"])
        self.assertEqual(h.goal_progress(D("0"), D("0"), None, date(2026, 1, 1))["percent"], 0.0)


class CsvSafetyTests(unittest.TestCase):
    def test_roundtrip(self):
        for text in ["=SUM(A1)", "+1", "-2", "@cmd", "normal", ""]:
            self.assertEqual(h.csv_unsafe(h.csv_safe(text)), text)
        self.assertEqual(h.csv_safe("=1+1"), "'=1+1")
        self.assertEqual(h.csv_safe(None), "")
        self.assertEqual(h.csv_unsafe("'hello"), "'hello")    # a normal apostrophe is kept


class MoneyCellTests(unittest.TestCase):
    def test_formats(self):
        cases = {"1,234.50": "1234.50", "$12": "12.00", "(45.00)": "-45.00", "12.50-": "-12.50",
                 "12,50": "12.50", "1.234,50": "1234.50", "1,500": "1500.00", "-7": "-7.00",
                 "₹ 2,00,000": "200000.00", "0": "0.00"}
        for raw, expected in cases.items():
            self.assertEqual(h.parse_money_cell(raw), D(expected), raw)
        for bad in ["", "abc", "NaN", None, "1e5x"]:
            self.assertIsNone(h.parse_money_cell(bad), bad)


class ImportTests(unittest.TestCase):
    def run_import(self, text, fmt="iso"):
        return h.parse_import(text, fmt, CATS)

    def test_basic_with_type_column(self):
        r = self.run_import("date,type,amount,category,note\n"
                            "2026-03-01,expense,12.50,Food,Lunch\n"
                            "2026-03-02,income,2000,Salary,March pay\n")
        self.assertEqual(r["problems"], [])
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["rows"][0], {"txn_date": date(2026, 3, 1), "type": "expense",
                                        "amount": D("12.50"), "category": "Food", "note": "Lunch"})

    def test_signed_amounts_without_type(self):
        r = self.run_import("Date,Amount,Description\n2026-03-01,-40.00,Groceries\n2026-03-02,100,Refund\n")
        self.assertEqual([x["type"] for x in r["rows"]], ["expense", "income"])
        self.assertEqual([x["amount"] for x in r["rows"]], [D("40.00"), D("100.00")])
        self.assertEqual(r["rows"][0]["note"], "Groceries")

    def test_debit_credit_columns(self):
        r = self.run_import("Date,Debit,Credit,Memo\n2026-03-01,25,,Coffee\n2026-03-02,,900,Pay\n2026-03-03,5,5,Both\n")
        self.assertEqual([x["type"] for x in r["rows"]], ["expense", "income"])
        self.assertEqual(len(r["problems"]), 1)
        self.assertIn("Row 4", r["problems"][0])

    def test_semicolon_delimiter_and_bom(self):
        r = self.run_import("\ufeffdate;amount;note\n2026-03-01;-5,50;Tea\n")
        self.assertEqual(r["rows"][0]["amount"], D("5.50"))

    def test_date_formats(self):
        r = self.run_import("date,amount\n31/12/2025,10\n", "dmy")
        self.assertEqual(r["rows"][0]["txn_date"], date(2025, 12, 31))
        r = self.run_import("date,amount\n12/31/2025,10\n", "mdy")
        self.assertEqual(r["rows"][0]["txn_date"], date(2025, 12, 31))
        r = self.run_import("date,amount\n31/12/2025,10\n", "iso")      # wrong format reported, not guessed
        self.assertEqual((r["rows"], len(r["problems"])), ([], 1))
        r = self.run_import("date,amount\n2026-03-01 14:30:00,10\n")    # timestamps are trimmed
        self.assertEqual(r["rows"][0]["txn_date"], date(2026, 3, 1))

    def test_categories_fall_back_to_other(self):
        r = self.run_import("date,amount,category\n2026-03-01,-5,food\n2026-03-01,-5,Gym\n2026-03-01,-5,\n")
        self.assertEqual([x["category"] for x in r["rows"]], ["Food", "Other", "Other"])
        self.assertEqual(r["remapped"], 1)   # only 'Gym' was a real mismatch

    def test_category_is_checked_against_the_right_type(self):
        r = self.run_import("date,type,amount,category\n2026-03-01,income,5,Food\n")
        self.assertEqual(r["rows"][0]["category"], "Other")   # Food is an expense category

    def test_bad_rows_reported_and_good_rows_kept(self):
        r = self.run_import("date,type,amount\nnot-a-date,expense,5\n2026-03-01,expense,abc\n"
                            "2026-03-01,sideways,5\n2026-03-01,expense,0\n2026-03-01,expense,7\n,,\n")
        self.assertEqual(len(r["rows"]), 1)
        self.assertEqual(len(r["problems"]), 4)

    def test_missing_columns_and_empty(self):
        self.assertIn("required columns", self.run_import("foo,bar\n1,2\n")["problems"][0])
        self.assertIn("required columns", self.run_import("date,note\n2026-01-01,x\n")["problems"][0])
        self.assertEqual(self.run_import("")["problems"], ["The file is empty."])

    def test_row_limit(self):
        body = "date,amount\n" + "2026-03-01,1\n" * 10
        r = h.parse_import(body, "iso", CATS, max_rows=4)
        self.assertEqual(len(r["rows"]), 4)
        self.assertIn("Stopped after", r["problems"][-1])

    def test_export_then_import_roundtrip(self):
        note = h.csv_safe("=HYPERLINK(1)")
        r = self.run_import(f"date,type,amount,category,note\n2026-03-01,expense,9.99,Food,{note}\n")
        self.assertEqual(r["rows"][0]["note"], "=HYPERLINK(1)")

    def test_huge_amount_rejected(self):
        r = self.run_import("date,amount\n2026-03-01,99999999999999\n")
        self.assertEqual((r["rows"], len(r["problems"])), ([], 1))


class DecodeTests(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(h.decode_upload("café".encode("utf-8-sig")), "café")
        self.assertEqual(h.decode_upload("café".encode("latin-1")), "café")


if __name__ == "__main__":
    unittest.main()