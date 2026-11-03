import tempfile
import unittest
from pathlib import Path

from churn_engine.ingest import SchemaError, load_customers, validate_customers

from .helpers import AS_OF, HEADER, csv_row, write_csv


class IngestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def load(self, rows, header=HEADER):
        return load_customers(write_csv(self.tmp / "c.csv", rows, header))

    def test_valid_rows_are_typed(self):
        clean, rejected = validate_customers(self.load([csv_row("C-1", contract_end="2026-12-01")]), AS_OF)
        self.assertTrue(rejected.empty)
        self.assertEqual(str(clean["mrr_usd"].dtype), "float64")
        self.assertEqual(str(clean["open_support_tickets"].dtype), "int64")
        self.assertIn("datetime64", str(clean["last_login_at"].dtype))
        self.assertIn("UTC", str(clean["last_login_at"].dtype))

    def test_quarantine_reasons(self):
        rows = [
            csv_row("C-1"),
            csv_row("C-1"),                                   # duplicado
            csv_row(""),                                      # sin ID
            csv_row("C-3", company=""),                       # sin empresa
            csv_row("C-4", mrr="abc"),                        # no numérico
            csv_row("C-5", tickets=-2),                       # negativo
            csv_row("C-6", tickets=1.5),                      # no entero
            csv_row("C-7", days_ago=-3),                      # login en el futuro
            csv_row("C-8", contract_end="no-es-fecha"),       # fecha de contrato inválida
            csv_row("C-9").replace("2026", "ayer", 1),        # fecha de login inválida
        ]
        clean, rejected = validate_customers(self.load(rows), AS_OF)
        self.assertEqual(list(clean["customer_id"]), ["C-1"])
        reasons = dict(zip(rejected["customer_id"], rejected["reject_reason"]))
        self.assertEqual(reasons["C-1"], "duplicate_customer_id")
        self.assertEqual(reasons[""], "missing_customer_id")
        self.assertEqual(reasons["C-3"], "missing_company_name")
        self.assertEqual(reasons["C-4"], "invalid_mrr_usd")
        self.assertEqual(reasons["C-5"], "negative_open_support_tickets")
        self.assertEqual(reasons["C-6"], "non_integer_open_support_tickets")
        self.assertEqual(reasons["C-7"], "last_login_in_future")
        self.assertEqual(reasons["C-8"], "invalid_contract_end_date")
        self.assertEqual(reasons["C-9"], "invalid_last_login_at")

    def test_rejected_rows_keep_original_values(self):
        _, rejected = validate_customers(self.load([csv_row("C-4", mrr="abc")]), AS_OF)
        self.assertEqual(rejected.iloc[0]["mrr_usd"], "abc")

    def test_missing_required_column(self):
        header = HEADER.replace("plan,", "")
        with self.assertRaisesRegex(SchemaError, "plan"):
            load_customers(write_csv(self.tmp / "c.csv", [], header))

    def test_bom_and_header_case(self):
        path = self.tmp / "bom.csv"
        path.write_text("\ufeff" + HEADER.upper() + "\n" + csv_row("C-1") + "\n", encoding="utf-8")
        clean, rejected = validate_customers(load_customers(path), AS_OF)
        self.assertEqual(len(clean), 1)
        self.assertTrue(rejected.empty)

    def test_optional_columns_are_added_when_absent(self):
        header = "customer_id,company_name,plan,mrr_usd,last_login_at,open_support_tickets,usage_last_30d,usage_prev_30d"
        row = ",".join(csv_row("C-1").split(",")[:8])
        clean, _ = validate_customers(load_customers(write_csv(self.tmp / "c.csv", [row], header)), AS_OF)
        self.assertEqual(clean.iloc[0]["hubspot_company_id"], "")

    def test_file_errors(self):
        with self.assertRaises(FileNotFoundError):
            load_customers(self.tmp / "no_existe.csv")
        other = self.tmp / "datos.json"
        other.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(SchemaError, "CSV"):
            load_customers(other)
        empty = self.tmp / "vacio.csv"
        empty.write_text("", encoding="utf-8")
        with self.assertRaises(SchemaError):
            load_customers(empty)


if __name__ == "__main__":
    unittest.main()
