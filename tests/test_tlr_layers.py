import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.modules.tlr.models import TlrRun
from app.modules.tlr.preprocessing import preprocess_typed
from app.modules.tlr.schemas import RunOptions
from tests.test_tlr import TestLLM
from tests.test_tlr import api as api


def artifact(kind, content, locator="fixture.txt", **structure):
    return SimpleNamespace(
        id="a", external_id="A", kind=kind, content=content, locator=locator, structure=structure
    )


@pytest.mark.parametrize("mode", ["artifact", "chunk", "method", "class"])
async def test_java_granularities_preserve_characters_and_offsets(mode):
    text = (
        "package demo;\n/** 登录服务 */ class Login {\n"
        "/** 用户登录 */ public boolean login(){ return true; }\n"
        "public void logout(){}\n}\n"
    )
    rows = await preprocess_typed(
        artifact("code", text, "Login.java"),
        "source",
        "r",
        RunOptions(source_preprocessor=mode, chunk_size=100),
        TestLLM(),
    )
    assert "".join(r.content for r in rows) == text
    assert all(text[r.start : r.end] == r.content for r in rows)
    assert all(r.processing["strategy"] == mode for r in rows)
    if mode == "method":
        assert len(rows) == 3
        assert rows[1].content.startswith("/** 用户登录 */")


async def test_auto_preserves_test_case_and_splits_natural_language():
    body = "Preconditions: login.\nSteps: submit.\nExpected results: success."
    opts = RunOptions(source_preprocessor="auto")
    test = await preprocess_typed(artifact("test_case", body), "source", "r", opts, TestLLM())
    assert len(test) == 1 and test[0].content == body
    natural = await preprocess_typed(
        artifact("natural_language", "First sentence. Second sentence."),
        "source",
        "r",
        opts,
        TestLLM(),
    )
    assert len(natural) == 2


async def test_reference_is_never_used_as_code():
    with pytest.raises(ValueError, match="only a reference"):
        await preprocess_typed(
            artifact("code", "SomeFile.java", content_status="reference_only"),
            "source",
            "r",
            RunOptions(),
            TestLLM(),
        )


async def test_run_skips_one_preprocessing_artifact_and_marks_partial(api):
    client, _, _ = api
    payload = {
        "tenant_id": "tenant-a",
        "project_id": "software-a",
        "version": "partial-preprocessing",
        "artifacts": [
            {
                "external_id": "R-good",
                "kind": "requirement",
                "revision": "v1",
                "content": "login is required.",
            },
            {
                "external_id": "R-ref",
                "kind": "code",
                "revision": "v1",
                "content": "Missing.java",
                "structure": {"content_status": "reference_only"},
            },
            {"external_id": "T", "kind": "test_case", "revision": "v1", "content": "login test"},
        ],
    }
    dataset = (await client.post("/api/v1/tlr/datasets", json=payload)).json()["data"]
    created = await client.post(
        "/api/v1/tlr/runs",
        json={
            "tenant_id": "tenant-a",
            "project_id": "software-a",
            "dataset_id": dataset["id"],
            "source_ids": ["R-good", "R-ref"],
            "target_ids": ["T"],
            "options": {"top_k": 1, "source_preprocessor": "auto", "target_preprocessor": "auto"},
        },
    )
    run_id = created.json()["data"]["id"]
    response = await client.post(
        f"/api/v1/tlr/runs/{run_id}/execute",
        params={"tenant_id": "tenant-a", "project_id": "software-a"},
    )
    assert response.status_code == 200, response.text
    run = response.json()["data"]
    assert run["status"] == "completed" and run["stage"] == "completed_with_errors"
    failure = run["manifest"]["node_failures"][0]
    assert failure["node_type"] == "artifact" and failure["external_id"] == "R-ref"


async def test_llm_structure_is_grounded_and_records_provenance():
    class Extractor:
        model = "test-extractor"
        quote = "Login component exposes the API"

        async def chat_json(self, **kwargs):
            return {"elements": [{"type": "component", "name": "Login", "quote": self.quote}]}

    provider = Extractor()
    a = artifact("architecture_design", "The Login component exposes the API.")
    opts = RunOptions(source_preprocessor="llm_structure")
    rows = await preprocess_typed(a, "source", "r", opts, provider)
    assert a.content[rows[0].start : rows[0].end] == provider.quote
    assert rows[0].processing["llm_model"] == "test-extractor"
    provider.quote = "invented component"
    with pytest.raises(ValueError, match="not grounded"):
        await preprocess_typed(a, "source", "r", opts, provider)


async def test_model_features_are_marked_derived():
    a = artifact(
        "architecture_model",
        json.dumps(
            {"components": [{"name": "Login", "operations": ["login"], "dependencies": ["Store"]}]}
        ),
    )
    rows = await preprocess_typed(
        a, "source", "r", RunOptions(source_preprocessor="auto"), TestLLM()
    )
    assert rows[0].processing["derived"] is True and "Store" in rows[0].content


async def test_layer_plan_defaults_scope_atomicity_and_full_pipeline(api):
    client, factory, _ = api
    payload = {
        "tenant_id": "layer-test",
        "project_id": "multi",
        "version": "v1",
        "artifacts": [
            {
                "external_id": "R",
                "kind": "requirement",
                "revision": "v1",
                "content": "login is required.",
            },
            {
                "external_id": "D",
                "kind": "design",
                "revision": "v1",
                "content": "Login design: login component",
                "structure": {
                    "parent_ids": ["R"],
                    "relations": [{"target_id": "T", "relation": "dataset_trace"}],
                },
            },
            {
                "external_id": "C-ref",
                "kind": "code",
                "revision": "v1",
                "content": "Some.java",
                "structure": {"content_status": "reference_only"},
            },
            {
                "external_id": "T",
                "kind": "test_case",
                "revision": "v1",
                "content": "login test: enter credentials, expect login success",
                "structure": {"parent_ids": ["D"]},
            },
        ],
    }
    ds = (await client.post("/api/v1/tlr/datasets", json=payload)).json()["data"]
    scope = {"tenant_id": "layer-test", "project_id": "multi"}
    layers = (await client.get(f"/api/v1/tlr/datasets/{ds['id']}/layers", params=scope)).json()[
        "data"
    ]
    assert layers["default_pairs"] == [["requirements", "design"], ["design", "verification"]]
    assert layers["excluded_ids"] == ["C-ref"]
    body = {
        **scope,
        "dataset_id": ds["id"],
        "options": {"top_k": 1, "source_preprocessor": "auto", "target_preprocessor": "auto"},
    }
    invalid = await client.post(
        "/api/v1/tlr/plans",
        json={**body, "pairs": [{"source": "requirements", "target": "implementation"}]},
    )
    assert invalid.status_code == 422
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(TlrRun)) == 0
    result = await client.post("/api/v1/tlr/plans", json=body)
    assert result.status_code == 200, result.text
    plan = result.json()["data"]
    assert len(plan["runs"]) == 2
    for run in plan["runs"]:
        assert run["config"]["plan_id"] == plan["plan_id"]
        response = await client.post(f"/api/v1/tlr/runs/{run['id']}/execute", params=scope)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "completed"
        assert response.json()["data"]["counts"]["links"] == 1
    hidden = await client.get(
        f"/api/v1/tlr/datasets/{ds['id']}/layers", params={**scope, "tenant_id": "other"}
    )
    assert hidden.status_code == 404
    inv = (await client.get(f"/api/v1/tlr/datasets/{ds['id']}/inventory", params=scope)).json()[
        "data"
    ]["items"]
    assert next(a for a in inv if a["external_id"] == "D")["structure"]["parent_ids"] == ["R"]


async def test_missing_keys_failure_is_actionable_and_persisted(api):
    from app.api.dependencies import get_embedding_provider, get_llm_provider
    from app.main import app
    from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
    from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
    from tests.test_tlr import SCOPE, create_run

    client, _, _ = api
    run, _ = await create_run(client)
    app.dependency_overrides[get_embedding_provider] = lambda: OpenAICompatibleEmbeddingProvider(
        base_url="http://unused", api_key="", model="unused", expected_dimension=2
    )
    app.dependency_overrides[get_llm_provider] = lambda: OpenAICompatibleLLMProvider(
        base_url="http://unused", api_key="", model="unused"
    )
    response = await client.post(f"/api/v1/tlr/runs/{run}/execute", params=SCOPE)
    assert response.status_code == 500
    assert "MODEL_API_KEY" in response.json()["msg"] and "LLM_API_KEY" in response.json()["msg"]
    saved = (await client.get(f"/api/v1/tlr/runs/{run}", params=SCOPE)).json()["data"]
    assert saved["status"] == "failed"
    assert saved["manifest"]["failure"]["error_type"] == "ConfigurationMissing"


async def test_auto_java_falls_back_explicitly_but_manual_method_is_strict():
    a = artifact("code", "class Broken { void login( {", "source.txt", language="java")
    rows = await preprocess_typed(
        a, "source", "r", RunOptions(source_preprocessor="auto"), TestLLM()
    )
    assert rows[0].content == a.content
    assert rows[0].processing["strategy"] == "chunk"
    assert rows[0].processing["fallback_from"] == "method"
    with pytest.raises(ValueError, match="syntax"):
        await preprocess_typed(
            a, "source", "r", RunOptions(source_preprocessor="method"), TestLLM()
        )
