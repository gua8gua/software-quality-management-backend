"""Small contract/deletion regressions; no LLM, embedding or real documents."""

from pathlib import Path
from runpy import run_path

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db
from app.modules.artifacts.models import Artifact, ContentNode, File, Project
from app.modules.artifacts.router import router
from app.modules.traceability.models import TlrRun


@pytest_asyncio.fixture
async def library():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa.event.listens_for(engine.sync_engine, "connect")
    def enable_fks(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with sessions() as session:
            yield session

    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    application.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        yield client, sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_library_types_scopes_and_deletion(library):
    client, sessions = library
    root = "/api/v1/projects"
    for project in ("one", "two"):
        response = await client.post(root, json={"project_id": project, "name": project})
        assert response.status_code == 201, response.text
    folder = (await client.post(f"{root}/one/folders", json={"name": "章节"})).json()["id"]
    child = (await client.post(f"{root}/one/folders", json={"name": "子目录", "parent_id": folder})).json()["id"]
    assert (await client.post(f"{root}/one/folders", json={"name": "章节"})).status_code == 409
    assert (await client.post(f"{root}/two/folders", json={"name": "错项目", "parent_id": folder})).status_code == 404
    assert (await client.delete(f"{root}/one/folders/{folder}")).status_code == 409

    import json

    async def upload(project, metadata, filename="srs.md", content="# 标题\n原始需求"):
        return await client.post(f"{root}/{project}/artifacts", data={"metadata": json.dumps(metadata)},
                                 files={"file": (filename, content.encode(), "text/plain")})

    response = await upload("one", {"external_id": "srs", "engineering_role": "requirement", "folder_id": child})
    assert response.status_code == 201, response.text
    source = response.json()["id"]
    assert response.json()["size_bytes"] == len("# 标题\n原始需求".encode())
    assert response.json()["uploaded_at"]
    listing = await client.get(f"{root}/one/artifacts")
    assert listing.status_code == 200, listing.text
    assert listing.json()[0]["size_bytes"] == len("# 标题\n原始需求".encode())
    assert listing.json()[0]["uploaded_at"] == response.json()["uploaded_at"]
    assert not set(listing.json()[0]).intersection({"payload", "text", "content", "inline_content"})
    response = await upload("one", {"external_id": "code", "modality": "code"}, "main.py", "print('ok')")
    assert response.status_code == 201, response.text
    assert response.json()["engineering_role"] is None
    assert (await upload("one", {"external_id": "old", "engineering_role": "test"})).status_code == 422
    assert (await upload("one", {"external_id": "missing-subtype"})).status_code == 422
    assert (await upload("one", {"external_id": "wrong-code", "modality": "code", "engineering_role": "design"})).status_code == 422
    assert (await upload("one", {"external_id": "old-field", "engineering_role": "design", "tags": []})).status_code == 422
    assert (await upload("one", {"external_id": "old-inline", "engineering_role": "design", "inline_content": None})).status_code == 422
    assert (await upload("two", {"external_id": "scope", "engineering_role": "design", "folder_id": child})).status_code == 404
    # Invalid scoped upload must not leave a File row after transaction closure.
    assert (await client.get(f"{root}/one/artifacts/{source}/content-tree")).status_code == 404
    response = await client.get(f"{root}/one/artifacts/{source}/original")
    assert response.content == "# 标题\n原始需求".encode()
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-content-type-options"] == "nosniff"
    async with sessions() as session:
        assert await session.scalar(sa.select(ContentNode.text).where(ContentNode.artifact_id == source, ContentNode.node_type == "paragraph")) == "原始需求"
    assert (await client.get(f"{root}/one?tenant_id=other")).status_code == 404
    assert (await client.get(f"{root}/two/artifacts/{source}/download")).status_code == 404
    assert (await client.get(f"{root}/one/artifacts/{source}/download")).content == "# 标题\n原始需求".encode()
    async with sessions() as session:
        session.add(TlrRun(id="derived", tenant_id="local", project_id="one"))
        session.add(TlrRun(id="unrelated", tenant_id="local", project_id="two"))
        await session.commit()
    assert (await client.delete(f"{root}/one/artifacts/{source}")).status_code == 204
    async with sessions() as session:
        assert await session.get(TlrRun, "derived") is None
        assert await session.get(TlrRun, "unrelated") is not None
        assert await session.scalar(sa.select(sa.func.count()).select_from(ContentNode).where(ContentNode.artifact_id == source)) == 0
        assert await session.scalar(sa.select(sa.func.count()).select_from(File)) == 1
    assert (await client.delete(f"{root}/one/folders/{child}")).status_code == 204
    assert (await client.delete(f"{root}/one")).status_code == 204
    async with sessions() as session:
        assert await session.get(Project, "two") is not None
        assert await session.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 0
        assert await session.scalar(sa.select(sa.func.count()).select_from(File)) == 0


def test_destructive_upgrade_retains_only_conforming_projects():
    old = sa.MetaData()
    for name in ("projects", "files", "content_nodes", "tlr_runs"):
        Base.metadata.tables[name].to_metadata(old)
    sa.Table("artifacts", old,
             sa.Column("id", sa.String(36), primary_key=True),
             sa.Column("project_id", sa.String(128), sa.ForeignKey("projects.id"), nullable=False),
             sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id")),
             sa.Column("external_id", sa.String(256), nullable=False),
             sa.Column("revision", sa.String(128), nullable=False, server_default="1"),
             sa.Column("engineering_role", sa.String(32), nullable=False, server_default="other"),
             sa.Column("modality", sa.String(32), nullable=False, server_default="text"),
             sa.Column("file_format", sa.String(32), nullable=False, server_default=""),
             sa.Column("language", sa.String(32), nullable=False, server_default=""),
             sa.Column("participation", sa.String(32), nullable=False, server_default="included"),
             sa.Column("locator", sa.Text(), nullable=False, server_default=""),
             sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
             sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
             sa.Column("inline_content", sa.Text()),
             sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
             sa.UniqueConstraint("project_id", "external_id"))
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        old.create_all(connection)
        connection.execute(old.tables["projects"].insert(), [
            {"id": "valid", "tenant_id": "local", "name": "valid"},
            {"id": "invalid", "tenant_id": "local", "name": "invalid"}])
        connection.execute(old.tables["artifacts"].insert(), [
            {"id": "keep", "project_id": "valid", "external_id": "SRS", "engineering_role": "requirement"},
            {"id": "remove", "project_id": "invalid", "external_id": "old", "engineering_role": "test"}])
        connection.execute(old.tables["content_nodes"].insert(), [
            {"id": "keep-node", "artifact_id": "keep", "node_type": "paragraph", "original_type": "text"},
            {"id": "remove-node", "artifact_id": "remove", "node_type": "paragraph", "original_type": "text"}])
        migration = run_path(str(Path(__file__).parents[1] / "migrations/versions/0012_project_library.py"))
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("artifacts")}
        assert "folder_id" in columns
        assert not columns.intersection({"participation", "locator", "tags", "metadata_json", "inline_content"})
        assert connection.execute(sa.text("SELECT id FROM projects")).scalars().all() == ["valid"]
        assert connection.execute(sa.text("SELECT id FROM content_nodes")).scalars().all() == ["keep-node"]
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.text("INSERT INTO artifacts(id,project_id,external_id,modality,engineering_role) VALUES ('bad','valid','bad','text','test')"))
    engine.dispose()
