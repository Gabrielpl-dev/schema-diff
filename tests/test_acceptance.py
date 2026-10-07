"""Black-box acceptance tests for Database Schema Diff (AC-1 .. AC-22).

Every test invokes the ``./app`` executable as a subprocess; no test imports
internal modules of the implementation.  The suite only needs the Python
standard library and runs with::

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BREAKING_WARNING = (
    "warning: dialect not specified; ambiguous syntax detected, defaulting to postgres"
)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([APP, *args], capture_output=True, text=True, cwd=str(ROOT))


def fixture(name: str) -> str:
    return str(FIXTURES / name)


def run_json(*args: str) -> dict:
    result = run(*args, "--json")
    return json.loads(result.stdout)


class SchemaDiffAcceptanceTests(unittest.TestCase):
    # AC-1
    def test_ac_01_identical_schemas(self):
        result = run("diff", fixture("ac01_old.sql"), fixture("ac01_new.sql"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "no differences")
        self.assertEqual(result.stderr, "")

    # AC-2
    def test_ac_02_comments_and_order(self):
        result = run("diff", fixture("ac02_old.sql"), fixture("ac02_new.sql"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "no differences")
        self.assertEqual(result.stderr, "")

    # AC-3
    def test_ac_03_table_added(self):
        result = run("diff", fixture("ac03_old.sql"), fixture("ac03_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("+ table.added posts", result.stdout)
        self.assertNotIn("breaking:", result.stdout)
        self.assertIn("CREATE TABLE posts", result.stdout)
        payload = run_json("diff", fixture("ac03_old.sql"), fixture("ac03_new.sql"))
        self.assertEqual(payload["summary"], {"added": 1, "removed": 0, "changed": 0})
        self.assertFalse(payload["breaking"])

    # AC-4
    def test_ac_04_table_removed(self):
        result = run("diff", fixture("ac04_old.sql"), fixture("ac04_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn("- table.removed users", result.stdout)
        self.assertIn(
            "- constraint.removed posts.__inline__posts__fk__user_id", result.stdout
        )
        migration = result.stdout
        self.assertIn("DROP CONSTRAINT", migration)
        self.assertLess(
            migration.index("DROP CONSTRAINT"), migration.index("DROP TABLE users")
        )
        self.assertNotIn("ALTER TABLE users", migration)

    # AC-5
    def test_ac_05_add_nullable_column(self):
        result = run("diff", fixture("ac05_old.sql"), fixture("ac05_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("+ column.added users.nickname", result.stdout)
        self.assertIn("ADD COLUMN nickname", result.stdout)

    # AC-6
    def test_ac_06_add_not_null_without_default(self):
        result = run("diff", fixture("ac06_old.sql"), fixture("ac06_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn("+ column.added users.email", result.stdout)
        payload = run_json("diff", fixture("ac06_old.sql"), fixture("ac06_new.sql"))
        self.assertTrue(payload["changes"][0]["breaking"])

    # AC-7
    def test_ac_07_add_not_null_with_default(self):
        result = run("diff", fixture("ac07_old.sql"), fixture("ac07_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("+ column.added users.active", result.stdout)
        self.assertNotIn("breaking:", result.stdout)

    # AC-8
    def test_ac_08_remove_column(self):
        result = run("diff", fixture("ac08_old.sql"), fixture("ac08_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn("- column.removed users.email", result.stdout)
        self.assertIn("DROP COLUMN email", result.stdout)

    # AC-9
    def test_ac_09_widen_type(self):
        result = run("diff", fixture("ac09_old.sql"), fixture("ac09_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("~ column.type_changed users.email", result.stdout)
        self.assertIn("TYPE VARCHAR(100)", result.stdout)
        payload = run_json("diff", fixture("ac09_old.sql"), fixture("ac09_new.sql"))
        self.assertEqual(payload["changes"][0]["before"]["type"], "varchar(50)")
        self.assertEqual(payload["changes"][0]["after"]["type"], "varchar(100)")
        self.assertFalse(payload["breaking"])
        # summary counts every entry of changes (spec 5.5), including the
        # compound "*.type_changed" kind.
        summary = payload["summary"]
        self.assertEqual(sum(summary.values()), len(payload["changes"]))

    # AC-10
    def test_ac_10_narrow_type(self):
        result = run("diff", fixture("ac10_old.sql"), fixture("ac10_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn("~ column.type_changed users.email", result.stdout)
        self.assertIn("breaking:", result.stdout)

    # AC-11
    def test_ac_11_not_null_changes(self):
        pair_a = run("diff", fixture("ac11_old.sql"), fixture("ac11_new_default.sql"))
        self.assertEqual(pair_a.returncode, 1)
        self.assertIn("~ column.nullability_changed users.active", pair_a.stdout)
        self.assertIn("~ column.default_added users.active", pair_a.stdout)

        pair_b = run("diff", fixture("ac11_old.sql"), fixture("ac11_new_no_default.sql"))
        self.assertEqual(pair_b.returncode, 3)
        self.assertIn("~ column.nullability_changed users.active", pair_b.stdout)
        self.assertIn("breaking:", pair_b.stdout)

    # AC-12
    def test_ac_12_default_removed(self):
        result = run("diff", fixture("ac12_old.sql"), fixture("ac12_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn("~ column.default_removed users.active", result.stdout)
        self.assertIn("DROP DEFAULT", result.stdout)

    # AC-13
    def test_ac_13_index_added(self):
        result = run("diff", fixture("ac13_old.sql"), fixture("ac13_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("+ index.added idx_users_email", result.stdout)
        self.assertIn("CREATE INDEX idx_users_email", result.stdout)

    # AC-14
    def test_ac_14_constraints_and_ordering(self):
        result = run("diff", fixture("ac14_old.sql"), fixture("ac14_new.sql"))
        self.assertEqual(result.returncode, 1)
        for expected in (
            "+ column.added users.email",
            "+ constraint.added comments.__inline__comments__fk__post_id",
            "+ constraint.added posts.__inline__posts__fk__user_id",
            "+ table.added comments",
        ):
            self.assertIn(expected, result.stdout)
        migration = result.stdout
        self.assertLess(
            migration.index("CREATE TABLE comments"), migration.index("REFERENCES posts")
        )
        payload = run_json("diff", fixture("ac14_old.sql"), fixture("ac14_new.sql"))
        self.assertEqual(payload["summary"], {"added": 4, "removed": 0, "changed": 0})

    # AC-15
    def test_ac_15_primary_key_removed(self):
        result = run("diff", fixture("ac15_old.sql"), fixture("ac15_new.sql"))
        self.assertEqual(result.returncode, 3)
        self.assertIn(
            "- constraint.removed users.__inline__users__id__primary_key", result.stdout
        )
        self.assertIn("~ column.nullability_changed users.id", result.stdout)
        self.assertIn("DROP CONSTRAINT", result.stdout)

    # AC-16
    def test_ac_16_mysql_dialect(self):
        result = run(
            "diff", fixture("ac16_old.sql"), fixture("ac16_new.sql"), "--dialect", "mysql"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("MODIFY COLUMN", result.stdout)
        self.assertNotIn("ALTER COLUMN", result.stdout)
        payload = run_json(
            "diff", fixture("ac16_old.sql"), fixture("ac16_new.sql"),
            "--dialect", "mysql",
        )
        self.assertEqual(payload["dialect"], "mysql")

    # AC-17
    def test_ac_17_ambiguous_dialect_warning(self):
        result = run("diff", fixture("ac17_old.sql"), fixture("ac17_new.sql"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), BREAKING_WARNING)
        self.assertNotIn(BREAKING_WARNING, result.stdout)
        payload = run_json("diff", fixture("ac17_old.sql"), fixture("ac17_new.sql"))
        self.assertEqual(payload["dialect"], "postgres")
        self.assertIn(BREAKING_WARNING, payload["warnings"])

    # AC-18
    def test_ac_18_unsupported_statement(self):
        result = run("diff", fixture("ac18_old.sql"), fixture("ac18_new.sql"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("error: unsupported statement at line"))
        self.assertIn("CREATE VIEW", result.stderr)

    # AC-19
    def test_ac_19_missing_file(self):
        result = run("diff", fixture("does_not_exist.sql"), fixture("ac19_new.sql"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("error:"))

    # AC-20
    def test_ac_20_json_is_pure(self):
        result = run("diff", fixture("ac13_old.sql"), fixture("ac13_new.sql"), "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        for field in (
            "version", "dialect", "breaking", "summary", "changes", "migration", "warnings",
        ):
            self.assertIn(field, payload)
        self.assertEqual(payload["version"], "0.1.0")
        self.assertEqual(payload["changes"][0]["kind"], "index.added")
        self.assertFalse(payload["breaking"])

    # AC-21
    def test_ac_21_version_and_help(self):
        version = run("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn("0.1.0", version.stdout)

        help_result = run("--help")
        self.assertEqual(help_result.returncode, 0)
        for token in ("diff", "--dialect", "--json"):
            self.assertIn(token, help_result.stdout)

    # AC-22
    def test_ac_22_determinism(self):
        args = ("diff", fixture("ac16_old.sql"), fixture("ac16_new.sql"), "--json")
        first = run(*args)
        second = run(*args)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.returncode, second.returncode)


if __name__ == "__main__":
    unittest.main()
