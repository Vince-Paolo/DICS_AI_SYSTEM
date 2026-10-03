"""
Delete IncidentResponse rows that are orphaned -- i.e. their incident_id no
longer points to any row in the incident table. This is the cleanup step
for orphans that existed *before* the ON DELETE CASCADE fix (migrate_add_cascade.py)
was applied, since that migration intentionally does not delete pre-existing
orphans automatically.

Because the cascade migration already gave incident_response's children
(task, resource, incident_message, post_incident_report) ON DELETE CASCADE
back to incident_response, deleting an orphaned response row here will
automatically and safely remove its associated tasks/resources/messages/
post-incident report too -- no manual cleanup of those needed.

Safe by design:
  - Takes a timestamped backup of the database file first.
  - Only ever deletes rows whose incident_id has no matching incident.
    It never touches a response with a valid incident.
  - Runs in one transaction: fully succeeds or fully rolls back.
  - Prints exactly what it's about to delete before doing so, and again
    afterward to confirm.

Usage:
    cd DICS_AI_SYSTEM-main
    python3 scripts/delete_orphaned_responses.py          # interactive, asks to confirm
    python3 scripts/delete_orphaned_responses.py --yes    # skip confirmation prompt
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


def get_sqlite_path():
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('sqlite:///'):
        print(f"This script only supports SQLite. Current URI: {uri}")
        sys.exit(1)
    return uri.replace('sqlite:///', '', 1)


def backup_database(db_path):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.pre_orphan_cleanup_{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def find_orphans(conn):
    return conn.execute("""
        SELECT ir.id, ir.incident_id, ir.commander_id, ir.status, ir.started_at
        FROM incident_response ir
        LEFT JOIN incident i ON ir.incident_id = i.id
        WHERE i.id IS NULL
    """).fetchall()


def main():
    skip_confirm = '--yes' in sys.argv or '-y' in sys.argv

    with app.app_context():
        db_path = get_sqlite_path()
        if not os.path.exists(db_path):
            print(f"No database found at {db_path}.")
            sys.exit(0)

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        orphans = find_orphans(conn)
        if not orphans:
            print("No orphaned incident_response rows found. Nothing to do.")
            conn.close()
            return

        print(f"Found {len(orphans)} orphaned incident_response row(s):\n")
        for row in orphans:
            resp_id, incident_id, commander_id, status, started_at = row
            task_count = conn.execute(
                "SELECT COUNT(*) FROM task WHERE incident_response_id=?", (resp_id,)
            ).fetchone()[0]
            resource_count = conn.execute(
                "SELECT COUNT(*) FROM resource WHERE incident_response_id=?", (resp_id,)
            ).fetchone()[0]
            message_count = conn.execute(
                "SELECT COUNT(*) FROM incident_message WHERE incident_response_id=?", (resp_id,)
            ).fetchone()[0]
            report_count = conn.execute(
                "SELECT COUNT(*) FROM post_incident_report WHERE incident_response_id=?", (resp_id,)
            ).fetchone()[0]
            print(f"  - Response #{resp_id} (status={status}, started={started_at}, "
                  f"commander_id={commander_id}) -> missing incident #{incident_id}")
            print(f"      will also cascade-delete: {task_count} task(s), "
                  f"{resource_count} resource(s), {message_count} message(s), "
                  f"{report_count} post-incident report(s)")

        if not skip_confirm:
            answer = input("\nDelete these orphaned response(s) and their cascaded "
                            "children? [y/N]: ").strip().lower()
            if answer != 'y':
                print("Aborted. No changes made.")
                conn.close()
                return

        backup_database(db_path)

        orphan_ids = [row[0] for row in orphans]
        placeholders = ",".join("?" for _ in orphan_ids)

        try:
            conn.execute("BEGIN")
            conn.execute(
                f"DELETE FROM incident_response WHERE id IN ({placeholders})",
                orphan_ids
            )
            conn.commit()
        except Exception:
            conn.rollback()
            print("Deletion FAILED and was rolled back. Your database is unchanged.")
            conn.close()
            raise

        remaining = find_orphans(conn)
        conn.close()

        print(f"\nDeleted {len(orphan_ids)} orphaned response(s) (IDs: {orphan_ids}) "
              f"and their cascaded children.")
        if remaining:
            print(f"WARNING: {len(remaining)} orphan(s) still remain -- please re-run "
                  f"or investigate.")
        else:
            print("Integrity check passed: no orphaned incident_response rows remain.")


if __name__ == '__main__':
    main()
