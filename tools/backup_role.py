"""Create or re-key the read-only `commons_backup` role and store its URL (decision 0033).

Usage: uv run python tools/backup_role.py [--project agent-commons-512623]

Reads the production database URL from Secret Manager, gives `commons_backup` a new random
password with SELECT on every table (also tables later migrations add) and read-only
sessions, then adds the role's URL as a new version of the
`agent-commons-production-backup-url` secret, which the nightly backup workflow reads. No
secret is printed.
"""

import argparse
import secrets
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROLE = "commons_backup"
OWNER_URL_ID = "agent-commons-production-database-url"
BACKUP_URL_ID = "agent-commons-production-backup-url"
GRANTS = (
    "GRANT CONNECT ON DATABASE {db} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}",
    "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO {role}",
    "ALTER ROLE {role} SET default_transaction_read_only = on",
)


def create_read_only_role(owner_url: str, role: str, password: str) -> str:
    """Create `role` (or reset its password) with read-only access; return its URL."""
    dbname = str(conninfo_to_dict(owner_url)["dbname"])
    with psycopg.connect(owner_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        for statement in GRANTS:
            conn.execute(
                sql.SQL(statement).format(db=sql.Identifier(dbname), role=sql.Identifier(role))
            )
    return make_conninfo(owner_url, user=role, password=password)


def _gcloud(project: str, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["gcloud", *args, f"--project={project}"],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main() -> None:
    """Re-key the role and store its URL as a new secret version."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="agent-commons-512623")
    project: str = parser.parse_args().project
    owner_url = _gcloud(
        project, "secrets", "versions", "access", "latest", f"--secret={OWNER_URL_ID}"
    )
    backup_url = create_read_only_role(owner_url, ROLE, secrets.token_urlsafe(24))
    _gcloud(project, "secrets", "versions", "add", BACKUP_URL_ID, "--data-file=-", stdin=backup_url)
    print(f"{ROLE} re-keyed; its URL is the newest version of {BACKUP_URL_ID}")


if __name__ == "__main__":
    main()
