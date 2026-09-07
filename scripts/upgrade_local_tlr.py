"""Apply additive local SQLite upgrades through 0006, with a database backup."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.modules.model_config.models import MODEL_CONFIG_TABLES
from app.modules.tlr.models import TlrArtifact, TlrDataset, TlrHierarchyNode


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
        hierarchy_missing = not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tlr_hierarchy_nodes'"
        ).fetchone()
        if not pending and not model_tables_missing and not hierarchy_missing:
            print("Local schema is current through 0006; no changes")
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
    if hierarchy_missing:
        metadata = MetaData()
        TlrDataset.__table__.to_metadata(metadata)
        TlrArtifact.__table__.to_metadata(metadata)
        TlrHierarchyNode.__table__.to_metadata(metadata)
        with create_engine(f"sqlite:///{path.as_posix()}").begin() as sync_connection:
            metadata.create_all(
                sync_connection,
                tables=[metadata.tables["tlr_hierarchy_nodes"]],
                checkfirst=True,
            )
        with sqlite3.connect(path) as connection:
            artifacts = list(
                connection.execute(
                    "SELECT id, dataset_id, external_id, kind, structure FROM tlr_artifacts"
                )
            )
            node_ids = {
                (dataset, external): str(uuid4())
                for _, dataset, external, _, _ in artifacts
            }
            definitions = []
            for ordinal, (
                artifact_id,
                dataset_id,
                external_id,
                kind,
                raw_structure,
            ) in enumerate(artifacts):
                structure = json.loads(raw_structure or "{}")
                parents = [
                    value
                    for value in structure.get("parent_ids", [])
                    if (dataset_id, value) in node_ids
                ]
                pure = kind == "package" or structure.get("content_status") == "reference_only"
                definitions.append(
                    (
                        node_ids[(dataset_id, external_id)],
                        dataset_id,
                        node_ids.get((dataset_id, parents[0])) if parents else None,
                        None if pure else artifact_id,
                        f"artifact:{external_id}",
                        structure.get("title") or structure.get("original_id") or external_id,
                        structure.get("original_type") or ("group" if pure else "artifact"),
                        ordinal,
                        json.dumps(
                            {
                                "source": "artifact.structure",
                                "legacy_external_id": external_id,
                                "all_parent_ids": parents,
                                "content_status": structure.get("content_status"),
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
            connection.executemany(
                "INSERT INTO tlr_hierarchy_nodes "
                "(id,dataset_id,parent_id,artifact_id,node_key,title,node_type,ordinal,"
                "metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                definitions,
            )
            connection.commit()
    print("Applied local upgrades through 0006; backup:", backup)


if __name__ == "__main__":
    main()
