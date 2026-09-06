from app.main import app


def test_business_endpoints_are_explicit() -> None:
    operations = {
        (method.upper(), path)
        for path, path_item in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in path_item
    }
    assert operations == {
        ("POST", "/api/v1/knowledge-bases/create"),
        ("POST", "/api/v1/documents/upload"),
        ("POST", "/api/v1/rag/retrieve"),
        ("POST", "/api/v1/tlr/datasets"),
        ("GET", "/api/v1/tlr/datasets/{dataset_id}/layers"),
        ("POST", "/api/v1/tlr/plans"),
        ("GET", "/api/v1/tlr/datasets/{dataset_id}"),
        ("GET", "/api/v1/tlr/datasets/{dataset_id}/artifacts"),
        ("POST", "/api/v1/tlr/runs"),
        ("POST", "/api/v1/tlr/runs/{run_id}/execute"),
        ("GET", "/api/v1/tlr/runs/{run_id}"),
        ("GET", "/api/v1/tlr/runs/{run_id}/outputs/{kind}"),
        ("POST", "/api/v1/tlr/runs/{run_id}/evaluations"),
        ("GET", "/api/v1/tlr/capabilities"),
        ("GET", "/api/v1/tlr/projects"),
        ("POST", "/api/v1/tlr/projects"),
        ("GET", "/api/v1/tlr/projects/{project_id}"),
        ("GET", "/api/v1/tlr/projects/{project_id}/datasets"),
        ("GET", "/api/v1/tlr/projects/{project_id}/runs"),
        ("POST", "/api/v1/tlr/projects/{project_id}/upload"),
        ("GET", "/api/v1/tlr/datasets/{dataset_id}/inventory"),
        ("GET", "/api/v1/tlr/artifacts/{artifact_id}"),
        ("GET", "/api/v1/tlr/artifacts/{artifact_id}/download"),
        ("GET", "/api/v1/tlr/runs/{run_id}/visualization"),
        ("GET", "/api/v1/tlr/runs/{run_id}/candidates/{candidate_id}"),
    }


def test_openapi_describes_unified_success_responses() -> None:
    schema = app.openapi()
    for name, path in schema["paths"].items():
        if name.endswith("/download"):
            continue  # File download intentionally returns binary, not ApiResponse JSON.
        for operation in path.values():
            success_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
            assert "$ref" in success_schema
