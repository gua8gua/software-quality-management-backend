"""Apply additive local SQLite upgrades through 0005, with a database backup."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.modules.model_config.models import MODEL_CONFIG_TABLES


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
        model_tables_missing = any(
            not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            for name in ("model_connections", "model_task_bindings")
        )
        if not pending and not model_tables_missing:
            print("Local schema is current through 0005; no changes")
            return
        directory = Path("artifacts/database-backups")
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / (
            "pre-local-upgrade-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".sqlite"
        )
        with sqlite3.connect(backup) as target:
            connection.backup(target)
        connection.execute("BEGIN IMMEDIATE")
        for table, column in pending:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} JSON NOT NULL DEFAULT '{{}}'"
            )
        connection.commit()
    if model_tables_missing:
        metadata = MetaData()
        for table in MODEL_CONFIG_TABLES:
            table.to_metadata(metadata)
        with create_engine(f"sqlite:///{path.as_posix()}").begin() as sync_connection:
            metadata.create_all(sync_connection, checkfirst=True)
    print("Applied local upgrades through 0005; backup:", backup)


if __name__ == "__main__":
    main()
