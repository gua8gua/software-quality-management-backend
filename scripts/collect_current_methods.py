"""Read-only evidence for segmentation/matching documentation; no model calls or secrets."""
import importlib.metadata
import json
import platform
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.core.config import get_settings
from app.modules.tlr.preprocessing import spans


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/tlr" + path, timeout=20) as response:
        return json.load(response)["data"]


def main():
    versions = {}
    for name in ["tree-sitter", "tree-sitter-java", "pypdf", "python-docx", "httpx", "pydantic", "sqlalchemy", "pgvector", "fastapi"]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not installed in this interpreter"
    settings = get_settings()
    result = {"collected_at": datetime.now().astimezone().isoformat(), "python": platform.python_version(),
              "packages": versions, "limits": {k: getattr(settings, k) for k in ["tlr_max_elements", "tlr_max_candidates", "tlr_max_comparisons"]}, "projects": []}
    result["segmentation_probes"] = []
    for text, mode, locator in [
        ("必须先校验。然后创建会话。", "sentence", "requirements.txt"),
        ("必须先校验。 然后创建会话。", "sentence", "requirements.txt"),
        ("def outer():\n    def inner():\n        return 1\n    return inner()\n", "method", "nested.py"),
    ]:
        result["segmentation_probes"].append({"mode": mode, "input": text, "units": [
            {"start": start, "end": end, "text": text[start:end]}
            for start, end in spans(text, mode, 2000, locator)
        ]})
    for project in get("/projects?tenant_id=local")["items"]:
        pid = project["id"]
        runs = get(f"/projects/{pid}/runs?tenant_id=local&limit=100")["items"]
        summary = {"project": pid, "runs": [{"id": r["id"], "dataset_id": r["dataset_id"], "status": r["status"], "stage": r["stage"], "options": r["config"].get("options", {}), "counts": r["counts"], "retriever": r.get("manifest", {}).get("retriever"), "models": {k: v.get("model_id") for k, v in r.get("manifest", {}).get("model_tasks", {}).items()}} for r in runs]}
        datasets = get(f"/projects/{pid}/datasets?tenant_id=local")["items"]
        if datasets:
            dataset = datasets[0]
            inventory = get(f"/datasets/{dataset['id']}/inventory?tenant_id=local&project_id={pid}&limit=500")
            summary["latest_dataset"] = {"id": dataset["id"], "version": dataset["version"], "total": inventory["total"], "sampled": len(inventory["items"]),
                "kinds_and_body_status": [{"kind": k[0], "content_status": k[1], "count": n} for k, n in Counter((a["kind"], (a.get("structure") or {}).get("content_status", "unspecified")) for a in inventory["items"]).items()]}
        result["projects"].append(summary)
    path = Path("artifacts/current-methods/2026-09-14.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(path), "python": result["python"], "packages": versions, "limits": result["limits"], "projects": [{"project": p["project"], "runs": len(p["runs"]), "configs": [list(k) + [n] for k, n in Counter((r["options"].get("source_preprocessor"), r["options"].get("target_preprocessor"), r["options"].get("top_k"), bool(r["options"].get("requirements_run_id"))) for r in p["runs"]).items()]} for p in result["projects"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
