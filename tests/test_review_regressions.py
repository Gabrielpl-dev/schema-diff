"""CLI regressions for the three findings in the PR review."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ReviewRegressionTests(unittest.TestCase):
    def diff(self, old, new):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("old.sql", "new.sql")]
            for path, text in zip(paths, (old, new), strict=True):
                path.write_text(text)
            result = subprocess.run(
                [str(ROOT / "app"), "diff", *map(str, paths), "--dialect", "postgres", "--json"],
                capture_output=True, text=True, cwd=ROOT,
            )
        self.assertIn(result.returncode, (0, 1, 3), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_numeric_integer_capacity_reduction_is_breaking(self):
        code, payload = self.diff(
            "CREATE TABLE prices(amount NUMERIC(5,2));",
            "CREATE TABLE prices(amount NUMERIC(6,4));",
        )
        self.assertEqual(code, 3)
        self.assertTrue(payload["breaking"])

    def test_unnamed_constraint_drop_resolves_catalog_name(self):
        for definition, kind, columns in (
            ("id INT PRIMARY KEY", "p", ["id"]),
            ("id INT UNIQUE", "u", ["id"]),
            ("id INT, other INT, UNIQUE(id, other)", "u", ["id", "other"]),
            ("id INT, FOREIGN KEY(id) REFERENCES parent(id)", "f", ["id"]),
        ):
            with self.subTest(definition=definition):
                prefix = "CREATE TABLE parent(id INT PRIMARY KEY);"
                _, payload = self.diff(
                    prefix + f"CREATE TABLE users({definition});",
                    prefix + "CREATE TABLE users(id INT, other INT);",
                )
                sql = "\n".join(payload["migration"])
                self.assertIn("pg_constraint", sql)
                self.assertIn(f"contype = '{kind}'", sql)
                for column in columns:
                    self.assertIn(f"'{column}'", sql)
                self.assertNotIn("__inline__", sql)

    def test_serial_creation_preserves_sequence_generating_type(self):
        for type_name in ("SERIAL", "BIGSERIAL"):
            for old, new in (
                ("", f"CREATE TABLE users(id {type_name} PRIMARY KEY);"),
                ("CREATE TABLE users(name TEXT);",
                 f"CREATE TABLE users(name TEXT, id {type_name});"),
            ):
                with self.subTest(type_name=type_name, old=old):
                    _, payload = self.diff(old, new)
                    sql = "\n".join(payload["migration"])
                    self.assertIn(f"id {type_name}", sql)
                    self.assertNotIn("nextval()", sql)


if __name__ == "__main__":
    unittest.main()
