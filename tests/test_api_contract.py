from app.main import app


def test_only_three_business_endpoints_are_exposed() -> None:
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
    }


def test_openapi_describes_unified_success_responses() -> None:
    schema = app.openapi()
    for path in schema["paths"].values():
        success_schema = path["post"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        assert "$ref" in success_schema
