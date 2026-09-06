import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, create_engine, event, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import get_embedding_provider, get_llm_provider
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app
from app.modules.tlr.importers import read_collection, read_gold
from app.modules.tlr.models import TLR_TABLES, TlrCandidate, TlrLink, TlrRun
from app.modules.tlr.pipeline import LissaRetriever, PythonRetriever, validate_vectors
from app.providers.embedding.base import EmbeddingProvider
from app.providers.llm.base import LLMProvider


class TestEmbedding(EmbeddingProvider):
    __test__ = False

    async def embed(self, texts):
        return [[1.0, 0.0] if "login" in t else [0.0, 1.0] for t in texts]


class TestLLM(LLMProvider):
    __test__ = False
    name, model = "test", "deterministic-test-only"

    def __init__(self):
        self.calls = 0
        self.fail_after = None

    async def chat_json(self, *, system_prompt, user_payload, **kwargs):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return {"related": "yes", "evidence": "invalid boolean must not become a link"}
        source, target = user_payload["source"]["content"], user_payload["target"]["content"]
        related = "login" in source and "login" in target
        return {
            "related": related,
            "evidence": "test fixture matching",
            "source_quote": "login" if related else "",
            "target_quote": "login" if related else "",
        }


@pytest_asyncio.fixture
async def api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tlr.sqlite'}")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for table in TLR_TABLES:
        table.to_metadata(metadata)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session():
        async with factory() as db:
            yield db

    llm = TestLLM()
    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, log_to_file=False)
    app.dependency_overrides[get_embedding_provider] = TestEmbedding
    app.dependency_overrides[get_llm_provider] = lambda: llm
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory, llm
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)
        await engine.dispose()


def dataset_payload():
    return {
        "tenant_id": "tenant-a",
        "project_id": "software-a",
        "version": "v1",
        "artifacts": [
            {
                "external_id": "R1",
                "kind": "requirement",
                "revision": "v1",
                "content": "login " * 30,
            },
            {"external_id": "C1", "kind": "code", "revision": "v1", "content": "def login(): pass"},
            {"external_id": "C2", "kind": "code", "revision": "v1", "content": "render image"},
        ],
    }


SCOPE = {"tenant_id": "tenant-a", "project_id": "software-a"}


async def create_run(client, options=None):
    response = await client.post("/api/v1/tlr/datasets", json=dataset_payload())
    assert response.status_code == 200, response.text
    dataset_id = response.json()["data"]["id"]
    response = await client.post(
        "/api/v1/tlr/runs",
        json={
            **SCOPE,
            "dataset_id": dataset_id,
            "source_ids": ["R1"],
            "target_ids": ["C1", "C2"],
            "options": options or {"top_k": 2, "source_preprocessor": "chunk", "chunk_size": 100},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"], dataset_id


async def test_http_pipeline_persists_and_aggregates_with_scope_isolation(api):
    client, factory, llm = api
    run_id, dataset_id = await create_run(client)
    response = await client.post(f"/api/v1/tlr/runs/{run_id}/execute", params=SCOPE)
    assert response.status_code == 200, response.text
    run = response.json()["data"]
    assert run["status"] == "completed"
    assert run["counts"] == {
        "source_elements": 2,
        "target_elements": 2,
        "candidates": 4,
        "classified": 4,
        "links": 1,
    }
    assert llm.calls == 4
    async with factory() as db:
        persisted = await db.get(TlrRun, run_id)
        assert persisted.manifest["prompt_version"] == "lissa-kiss-json-evidence-v1"
        links = list(await db.scalars(select(TlrLink).where(TlrLink.run_id == run_id)))
        assert len(links) == 1 and len(links[0].evidence_candidate_ids) == 2
        candidates = list(
            await db.scalars(select(TlrCandidate).where(TlrCandidate.run_id == run_id))
        )
        assert sorted(c.decision for c in candidates) == [
            "related",
            "related",
            "unrelated",
            "unrelated",
        ]
    for path in [
        f"datasets/{dataset_id}",
        f"datasets/{dataset_id}/artifacts",
        f"runs/{run_id}",
        f"runs/{run_id}/outputs/links",
    ]:
        hidden = await client.get(f"/api/v1/tlr/{path}", params={**SCOPE, "tenant_id": "other"})
        assert hidden.status_code == 404
    duplicate = await client.post(f"/api/v1/tlr/runs/{run_id}/execute", params=SCOPE)
    assert duplicate.status_code == 409 and llm.calls == 4
    page = await client.get(
        f"/api/v1/tlr/runs/{run_id}/outputs/candidates", params={**SCOPE, "limit": 1}
    )
    assert page.json()["data"]["total"] == 4
    assert len(page.json()["data"]["items"]) == 1


async def test_invalid_model_output_preserves_partial_evidence_and_failed_status(api):
    client, factory, llm = api
    llm.fail_after = 1
    run_id, _ = await create_run(client)
    response = await client.post(f"/api/v1/tlr/runs/{run_id}/execute", params=SCOPE)
    assert response.status_code == 500
    async with factory() as db:
        run = await db.get(TlrRun, run_id)
        assert run.status == "failed" and run.stage == "classification"
        assert run.counts["classified"] == 1
        candidates = list(
            await db.scalars(select(TlrCandidate).where(TlrCandidate.run_id == run_id))
        )
        assert sum(c.decision == "pending" for c in candidates) == 3
        assert not list(await db.scalars(select(TlrLink).where(TlrLink.run_id == run_id)))


async def test_gold_is_evaluation_only_and_measures_retrieval_misses(api):
    client, _, llm = api
    run_id, _ = await create_run(client, {"top_k": 1})
    assert (
        await client.post(f"/api/v1/tlr/runs/{run_id}/execute", params=SCOPE)
    ).status_code == 200
    response = await client.post(
        f"/api/v1/tlr/runs/{run_id}/evaluations",
        params=SCOPE,
        json={
            "gold_links": [
                {"source_id": "R1", "target_id": "C1"},
                {"source_id": "R1", "target_id": "C2"},
            ],
            "provenance": {"source": "test"},
        },
    )
    assert response.status_code == 200, response.text
    metrics = response.json()["data"]["metrics"]
    assert metrics["precision"] == 1 and metrics["recall"] == 0.5
    assert metrics["candidate_recall"] == 0.5 and metrics["unretrieved_gold_count"] == 1
    assert llm.calls == 1
    bad = await client.post(
        f"/api/v1/tlr/runs/{run_id}/evaluations",
        params=SCOPE,
        json={"gold_links": [{"source_id": "UNKNOWN", "target_id": "C1"}]},
    )
    assert bad.status_code == 422


async def test_dataset_and_run_validation(api):
    client, _, _ = api
    payload = dataset_payload()
    payload["artifacts"].append(payload["artifacts"][0])
    assert (await client.post("/api/v1/tlr/datasets", json=payload)).status_code == 422
    run_id, dataset_id = await create_run(client)
    bad = await client.post(
        "/api/v1/tlr/runs",
        json={**SCOPE, "dataset_id": dataset_id, "source_ids": ["MISSING"], "target_ids": ["C1"]},
    )
    assert bad.status_code == 422
    hidden = await client.post(
        f"/api/v1/tlr/runs/{run_id}/execute", params={**SCOPE, "project_id": "other"}
    )
    assert hidden.status_code == 404


@pytest.mark.parametrize("vectors", [[[0, 0]], [[float("nan"), 1]], [[1, 0], [1]], []])
def test_invalid_embeddings(vectors):
    with pytest.raises(ValueError):
        validate_vectors(vectors, 1)


def test_importer_rejects_missing_body_and_path_escape_and_preserves_first_gold(tmp_path):
    xml = tmp_path / "source.xml"
    template = (
        "<artifacts_collection><collection_info><content_location>external</content_location>"
        "</collection_info><artifacts><artifact><id>R1</id><content>{}</content>"
        "</artifact></artifacts></artifacts_collection>"
    )
    xml.write_text(template.format("missing.txt"))
    with pytest.raises(ValueError, match="Missing artifact body"):
        read_collection(xml, "requirement", "v1")
    xml.write_text(template.format("../outside.txt"))
    with pytest.raises(ValueError, match="escapes"):
        read_collection(xml, "requirement", "v1")
    gold = tmp_path / "answer.csv"
    gold.write_text("R1,C1\nR2,C2\n")
    assert read_gold(gold)[0].source_id == "R1"


def test_migration_upgrade_schema_matches_models_and_downgrades():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20260905_0002_tlr.py"
    spec = importlib.util.spec_from_file_location("tlr_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    next_spec = importlib.util.spec_from_file_location(
        "project_migration", path.parent / "20260905_0003_project_files.py"
    )
    next_migration = importlib.util.module_from_spec(next_spec)
    next_spec.loader.exec_module(next_migration)
    structure_spec = importlib.util.spec_from_file_location(
        "structure_migration", path.parent / "20260906_0004_tlr_structure.py"
    )
    structure_migration = importlib.util.module_from_spec(structure_spec)
    structure_spec.loader.exec_module(structure_migration)
    model_spec = importlib.util.spec_from_file_location(
        "model_config_migration", path.parent / "20260906_0005_model_config.py"
    )
    model_migration = importlib.util.module_from_spec(model_spec)
    model_spec.loader.exec_module(model_migration)
    metadata = MetaData()
    for table in TLR_TABLES:
        table.to_metadata(metadata)
    with create_engine("sqlite://").begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            next_migration.upgrade()
            structure_migration.upgrade()
            model_migration.upgrade()
            assert set(inspect(connection).get_table_names()) == set(metadata.tables)
            assert compare_metadata(context, metadata) == []
            model_migration.downgrade()
            structure_migration.downgrade()
            next_migration.downgrade()
            migration.downgrade()
            assert inspect(connection).get_table_names() == []


async def test_original_lissa_retrieval_matches_python_when_built():
    from types import SimpleNamespace

    settings = Settings(_env_file=None)
    if not await asyncio.to_thread(Path(settings.tlr_lissa_jar).is_file):
        pytest.skip("Build original LiSSA dependency to run Java integration test")
    sources = [SimpleNamespace(id="s", embedding=[1.0, 0.0])]
    targets = [
        SimpleNamespace(id="t1", embedding=[0.0, 1.0]),
        SimpleNamespace(id="t2", embedding=[1.0, 0.0]),
    ]
    java = LissaRetriever(settings.tlr_lissa_jar, settings.tlr_lissa_bridge_dir, "java", 30)
    assert await java.retrieve(sources, targets, 2) == await PythonRetriever().retrieve(
        sources, targets, 2
    )
