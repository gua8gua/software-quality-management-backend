"""Apply additive 0004 to the configured local SQLite TLR store, with SQLite backup."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings


def main():
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise SystemExit("This helper is SQLite-only; PostgreSQL uses alembic upgrade head")
    path = Path(url.database).resolve()
    if not path.is_file():
        raise SystemExit("Database missing; refusing to create an empty replacement")
    with sqlite3.connect(path) as connection:
        existing = {
            t: {r[1] for r in connection.execute(f"PRAGMA table_info({t})")}
            for t in ["tlr_artifacts", "tlr_elements"]
        }
        if any(not columns for columns in existing.values()):
            raise SystemExit("Expected TLR tables missing")
        pending = [
            (t, c)
            for t, c in [("tlr_artifacts", "structure"), ("tlr_elements", "processing")]
            if c not in existing[t]
        ]
        if not pending:
            print("0004 columns already present; no changes")
            return
        directory = Path("artifacts/database-backups")
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / (
            "pre-0004-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".sqlite"
        )
        with sqlite3.connect(backup) as target:
            connection.backup(target)
        connection.execute("BEGIN IMMEDIATE")
        for table, column in pending:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} JSON NOT NULL DEFAULT '{{}}'"
            )
        connection.commit()
        print("Applied additive 0004; backup:", backup)


if __name__ == "__main__":
    main()
