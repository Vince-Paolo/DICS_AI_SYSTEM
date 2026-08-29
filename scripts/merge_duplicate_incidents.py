"""
Merge duplicate Incident rows that were created for the same real-world
external event, back when scheduler.py's monitor_earthquakes /
monitor_floods_gdacs / monitor_volcanoes_eonet de-duplicated on a rolling
6-hour window from Incident.created_at instead of the source's own event id
(external_event_id, added after this bug was found). That old logic let the
same physical earthquake/flood/volcanic event re-trigger a brand new
duplicate incident every time the prior row aged past 6 hours, for as long
as the external feed (USGS / GDACS / EONET) kept reporting it.

The code fix (already applied) stops *new* duplicates from being created.
It cannot retroactively clean up rows created before it was deployed --
that's what this script is for. You identify which existing incident IDs
are duplicates of each other (e.g. by matching hazard_type/location/level
in the admin alerts or EOC incident monitoring page), and this script
merges them into one canonical row, re-pointing every dependent record
first so nothing is silently lost.

Safe by design:
  - Takes a timestamped backup of the database file first.
  - Dry-run by default: prints exactly what it would do and makes no
    changes until you pass --apply.
  - Refuses to merge if more than one of the given incidents has its own
    IncidentResponse (that 1:1 relationship can't be collapsed automatically
    -- you'd be choosing which response "wins", which is a judgment call
    this script won't make for you). Resolve that manually first, then
    re-run.
  - Runs the actual merge in one transaction: fully succeeds or fully
    rolls back.

Usage:
    cd DICS_AI_SYSTEM-main
    # Preview only -- no changes made:
    python3 scripts/merge_duplicate_incidents.py --keep 1 --merge 2 3

    # Actually perform the merge (after reviewing the preview):
    python3 scripts/merge_duplicate_incidents.py --keep 1 --merge 2 3 --apply
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Tables with a foreign key into incident.id, and whether that FK is
# effectively 1:1 (at most one row per incident) or 1:many.
DEPENDENT_TABLES_ONE_TO_ONE = [
    ('incident_response', 'incident_id'),
]
DEPENDENT_TABLES_ONE_TO_MANY = [
    ('incident_report', 'incident_id'),
    ('resource_request', 'incident_id'),
    ('ai_recommendation', 'incident_id'),
    ('alert', 'incident_id'),
    ('report', 'incident_id'),
    ('message', 'incident_id'),
]


def get_sqlite_path():
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    if not uri.startswith('sqlite:///'):
        print(f"This script only supports SQLite. Current URI: {uri}")
        sys.exit(1)
    return uri.replace('sqlite:///', '', 1)


def ensure_incident_schema(conn):
    columns = [row[1] for row in conn.execute("PRAGMA table_info(incident)").fetchall()]
    if 'external_event_id' not in columns:
        print("Incident table is missing external_event_id; adding it automatically before merge.")
        conn.execute("ALTER TABLE incident ADD COLUMN external_event_id VARCHAR(255)")
        conn.commit()


def backup_database(db_path):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f"{db_path}.pre_incident_merge_{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def fetch_incident(conn, incident_id):
    row = conn.execute(
        "SELECT id, hazard_type, location, level, status, alert, external_event_id, "
        "citizen_report_id, created_at FROM incident WHERE id=?",
        (incident_id,),
    ).fetchone()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--keep', type=int, required=True, help='Incident ID to keep (the canonical row).')
    parser.add_argument('--merge', type=int, nargs='+', required=True, help='Incident ID(s) to merge into --keep and delete.')
    parser.add_argument('--apply', action='store_true', help='Actually perform the merge. Without this flag, only a preview is printed.')
    args = parser.parse_args()

    keep_id = args.keep
    merge_ids = list(dict.fromkeys(args.merge))  # de-dupe, preserve order
    if keep_id in merge_ids:
        print(f"--keep {keep_id} cannot also appear in --merge.")
        sys.exit(1)

    with app.app_context():
        db_path = get_sqlite_path()
        if not os.path.exists(db_path):
            print(f"No database found at {db_path}.")
            sys.exit(0)

        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_incident_schema(conn)

        keep_row = fetch_incident(conn, keep_id)
        if keep_row is None:
            print(f"--keep incident #{keep_id} does not exist.")
            sys.exit(1)

        merge_rows = []
        for mid in merge_ids:
            row = fetch_incident(conn, mid)
            if row is None:
                print(f"--merge incident #{mid} does not exist.")
                sys.exit(1)
            merge_rows.append(row)

        print(f"Keeping incident #{keep_id}:")
        print(f"  {keep_row}")
        print(f"\nMerging {len(merge_rows)} incident(s) into #{keep_id}, then deleting them:")
        for row in merge_rows:
            print(f"  {row}")

        # Guard against collapsing two active IncidentResponse records into
        # one incident_id -- IncidentResponse is semantically 1:1 with
        # Incident (models.py: uselist=False), so this needs a human decision.
        all_ids = [keep_id] + merge_ids
        placeholders = ",".join("?" for _ in all_ids)
        response_rows = conn.execute(
            f"SELECT id, incident_id, commander_id, status FROM incident_response "
            f"WHERE incident_id IN ({placeholders})",
            all_ids,
        ).fetchall()
        if len(response_rows) > 1:
            print(
                f"\nABORTING: {len(response_rows)} of these incidents already have their own "
                f"IncidentResponse -- merging would collapse two active responses onto one "
                f"incident, which this script won't decide for you:"
            )
            for r in response_rows:
                print(f"  {r}")
            print(
                "\nResolve this manually first (e.g. close/reassign one of the responses), "
                "then re-run this script."
            )
            sys.exit(1)

        if not args.apply:
            print("\nDry run only -- no changes made. Re-run with --apply to perform this merge.")
            conn.close()
            return

        backup_database(db_path)

        try:
            conn.execute("BEGIN")
            for table, fk_column in DEPENDENT_TABLES_ONE_TO_ONE + DEPENDENT_TABLES_ONE_TO_MANY:
                merge_placeholders = ",".join("?" for _ in merge_ids)
                conn.execute(
                    f"UPDATE {table} SET {fk_column}=? WHERE {fk_column} IN ({merge_placeholders})",
                    [keep_id] + merge_ids,
                )
            delete_placeholders = ",".join("?" for _ in merge_ids)
            conn.execute(f"DELETE FROM incident WHERE id IN ({delete_placeholders})", merge_ids)
            conn.commit()
        except Exception:
            conn.rollback()
            print("Merge FAILED and was rolled back. Your database is unchanged.")
            conn.close()
            raise

        remaining = [mid for mid in merge_ids if fetch_incident(conn, mid) is not None]
        conn.close()

        print(f"\nMerged and deleted incident(s) {merge_ids} into #{keep_id}.")
        if remaining:
            print(f"WARNING: {remaining} still present -- please investigate.")
        else:
            print("Integrity check passed: all merged incident rows are gone.")


if __name__ == '__main__':
    main()
