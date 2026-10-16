# Deploying DICS AI System to Railway

This project runs on Railway with Railway Postgres. Configure the service with
the Railway Postgres `DATABASE_URL`, a strong `SECRET_KEY`, and the S3 storage
variables documented below for durable uploaded photos.

The application backup page exports Railway Postgres using `pg_dump`. Local
SQLite backups use Python's built-in SQLite backup API, so a successful local
export does not verify that the PostgreSQL client is installed in production.

For the Railpack builder, the included `railpack.json` installs
`postgresql-client` in the final runtime image while retaining Railpack's
generated runtime packages. Commit this file and redeploy the application
service. Alternatively, set `RAILPACK_DEPLOY_APT_PACKAGES` to
`... postgresql-client` in that service's Railway variables and redeploy.
See the [Railpack package configuration](https://railpack.com/guides/installing-packages/).
The included `nixpacks.toml` supplies the client for deployments using Nixpacks.

After deployment, run `pg_dump --version` in the application service's shell
(not the Postgres service) and retry the backup export. If it still fails,
inspect the application logs for `Failed to create backup`. The installed
`pg_dump` major version must be at least the database server's major version;
if necessary, install a compatible versioned client package available in the
builder's Apt repositories. The generic `postgresql-client` package does not
guarantee compatibility with every server version.

The application already creates `instance/` at startup, including when using
Postgres; that folder does not need to be committed to the repository.

## Railway Postgres backup and restore verification

Two backup mechanisms exist for this project. Only the first has actually
been run end-to-end and confirmed to work; the second is a convenience layer
that has not yet been drilled and should not be treated as verified until it
has.

### 1. `pg_dump` / `pg_restore` drill (verified, run this one)

This is a logical dump-and-restore, run from inside the Postgres service's
own Railway console (Postgres service → **Console** tab) rather than from a
local machine — no `railway` CLI, tunnel, or local Postgres client install is
required, since the console already has `psql`/`pg_dump`/`pg_restore` on the
service's own image.

1. Discover the connection details actually set in this environment (don't
   assume names/values — confirm them):
   ```sh
   env | grep -Ei 'PG|POSTGRES|DATABASE'
   ```
2. Take a backup:
   ```sh
   pg_dump "$DATABASE_URL" --format=custom --no-owner \
     --file=/tmp/backup-$(date +%Y%m%d-%H%M%S).dump
   ```
3. Restore into a throwaway database on the same server — never into the
   real database:
   ```sh
   createdb -h localhost -U "$POSTGRES_USER" restore_drill
   pg_restore -h localhost -U "$POSTGRES_USER" -d restore_drill \
     --no-owner --exit-on-error /tmp/backup-<timestamp>.dump
   ```
4. Confirm the data actually came back, don't just check the restore command
   exited cleanly:
   ```sh
   psql -h localhost -U "$POSTGRES_USER" -d restore_drill \
     -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"
   ```
   Compare row counts against the real `railway` database.
5. Clean up the scratch database:
   ```sh
   dropdb -h localhost -U "$POSTGRES_USER" restore_drill
   ```

The console's filesystem is ephemeral, so the `.dump` file disappears once
the session ends — that's fine for this drill (the point is proving the dump
and restore both work, not keeping this specific file), but it means this
console session is not itself an ongoing backup strategy. Repeat this drill
periodically, not just once before submission, since a passing drill from
weeks ago doesn't prove today's schema restores cleanly.

### 2. Railway native volume snapshots (convenience layer, not yet drilled)

Railway's **Backups** tab on the Postgres service can also snapshot the
attached volume on a schedule (daily/weekly/monthly retention) with a
one-click restore. This is a reasonable extra safety net for routine
"oops" recovery, but as of this writing nobody has actually clicked
**Restore** on one of these snapshots and confirmed the data comes back
correctly in this project — so treat it as configured, not verified, until
that drill has actually been run once:

1. Postgres service → **Backups** tab → create a manual backup, confirm it
   completes.
2. **Restore** it, review the staged change, **Deploy**, then run the same
   spot-checks as step 4 above against the restored service.

Two things worth knowing before relying on this instead of the `pg_dump`
drill: a volume snapshot can only be restored within the same Railway
project and environment — it doesn't prove your data is portable off
Railway the way a logical dump does — and it restores the whole volume,
not a scratch copy, so there's no equivalent of testing into a throwaway
database first.

## Uploaded photos and backups

Citizen photos and responder media are stored through the configured
S3-compatible backend when `FILE_STORAGE_BACKEND=s3`. The browser accesses
files through the application upload route; bucket credentials are never sent
to the browser. Back up or retain the object-store bucket separately from
Railway Postgres dumps.

For local development, `FILE_STORAGE_BACKEND=local` stores files under
`instance/uploads`. Those files are not durable in a deployed container.

## Operational notes

- Keep Railway and database credentials in Railway variables or a local secret
  manager. Never put them in source control or shell history.
- Run `flask --app app db upgrade` after deploying revisions that add
  migrations.
- Analytics-only application changes do not add database schema and require no
  migration; continue to run the upgrade command whenever a release includes
  an Alembic revision.
- Verify the application health endpoint and log in after each production
  deployment.
- Keep at least one verified backup outside Railway.


## Email (SMTP)

Password-reset links are sent through SMTP. Email verification codes continue to
use the configured email backend. SMTP requires Railway Pro or higher.

Set these in Railway -> your service -> Variables, then redeploy:

| Variable | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS; 465 = SSL) |
| `SMTP_USERNAME` | `you@gmail.com` |
| `SMTP_PASSWORD` | Gmail App Password (16 chars; spaces are accepted) |
| `SMTP_FROM_EMAIL` | `you@gmail.com` (must match the account for Gmail) |

For Gmail, enable 2-Step Verification and create an App Password for the
account. Set all five SMTP variables above in Railway -> your service ->
Variables, then redeploy. Password-reset delivery uses SMTP even if Resend is
configured for verification emails.

Test the selected email backend from the Railway shell:
`flask --app app test-email you@example.com` (prints active settings without
the password and the real error on failure).
