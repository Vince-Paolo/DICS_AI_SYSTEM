"""
One-time migration: rebuild incident_response, task, incident_message,
post_incident_report, and resource with ON DELETE CASCADE foreign keys,
without losing any existing data.

Why this is needed:
SQLite does not support ALTER TABLE ... ADD CONSTRAINT for foreign keys.
A FK's ON DELETE behavior can only be set when the table is created. Since
models.py now defines these FKs with ondelete='CASCADE', any database file
created *before* this change still has the old (non-cascading) schema and
needs to be rebuilt to match.

What it does, per table, in a single transaction:
  1. Renames the existing table to <name>__migrate_old
  2. Creates the new table using the exact DDL SQLAlchemy generates from the
     current models.py (so it always matches your models, including the new
     ondelete='CASCADE' rules)
  3. Copies all rows across, column by column
  4. Drops the old table

A timestamped .db file backup is taken first, and the whole migration runs
in one transaction so it either fully succeeds or leaves your original
database untouched.

Usage:
    cd DICS_AI_SYSTEM-main
    python3 scripts/migrate_add_cascade.py
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.schema import CreateTable

from app import app
from models import db, IncidentResponse, Task, IncidentMessage, PostIncidentReport, Resource

# Order matters only in that we recreate parent-ish tables before children
# for readability; FK checks are OFF for the duration of the migration so
# it will work regardless of order.
TABLES_TO_REBUILD = [
    IncidentResponse.__table__,
    Task.__table__,
    IncidentMessage.__table__,
    PostIncidentReport.__table__,
    Resource.__table__,
]


def get_sqlite_path():
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('sqlite:///'):
        print(f"This migration only supports SQLite. Current URI: {uri}")
        print("If you're on Postgres/MySQL in production, ALTER TABLE with a "
              "FK ondelete rule works natively there -- no rebuild needed; "
              "just run db.create_all() equivalent / a proper migration tool.")
        sys.exit(1)
    return uri.replace('sqlite:///', '', 1)


def backup_database(db_path):
    if not os.path.exists(db_path):
        print(f"No database found at {db_path} -- nothing to migrate.")
        sys.exit(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.pre_cascade_migration_{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def rebuild_table(conn, table):
    name = table.name
    old_name = f"{name}__migrate_old"
    columns = [c.name for c in table.columns]
    column_list = ", ".join(f'"{c}"' for c in columns)

    print(f"  - Rebuilding '{name}'...")

    conn.execute(f'ALTER TABLE "{name}" RENAME TO "{old_name}"')

    create_sql = str(CreateTable(table).compile(dialect=db.engine.dialect))
    conn.execute(create_sql)

    conn.execute(
        f'INSERT INTO "{name}" ({column_list}) '
        f'SELECT {column_list} FROM "{old_name}"'
    )

    copied = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    original = conn.execute(f'SELECT COUNT(*) FROM "{old_name}"').fetchone()[0]
    if copied != original:
        raise RuntimeError(
            f"Row count mismatch for '{name}': copied {copied}, expected {original}."
        )

    conn.execute(f'DROP TABLE "{old_name}"')
    print(f"    OK -- {copied} row(s) preserved.")


def main():
    with app.app_context():
        db_path = get_sqlite_path()
        backup_database(db_path)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN")

            for table in TABLES_TO_REBUILD:
                if not table_exists(conn, table.name):
                    print(f"  - Skipping '{table.name}' (table doesn't exist yet).")
                    continue
                rebuild_table(conn, table)

            conn.commit()
            print("\nMigration committed.")
        except Exception:
            conn.rollback()
            print("\nMigration FAILED and was rolled back. Your database is unchanged.")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

        # Sanity check: confirm there are no dangling FK references.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()

        if violations:
            print("\nWARNING: existing orphaned rows were found (from before this "
                  "fix). These are NOT deleted automatically. Review them:")
            for v in violations:
                print(f"  table={v[0]} rowid={v[1]} refers to missing "
                      f"{v[2]}.{v[3] if len(v) > 3 else ''}")
        else:
            print("Integrity check passed: no orphaned rows found.")

        print("\nDone. New rows created from now on will correctly cascade-delete "
              "when their parent incident/response is removed.")


if __name__ == '__main__':
    main()
