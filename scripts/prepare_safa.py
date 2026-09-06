"""Convert original SAFA V1 trees without inventing source bodies or hierarchy semantics."""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from app.modules.tlr.schemas import DatasetInput

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/SAREC-Lab/SAFA-Artifacts.git"
TYPES = {
    "Hazard": "hazard",
    "Requirement": "requirement",
    "SafetyRequirement": "requirement",
    "ProcessRequirement": "requirement",
    "DesignDefinition": "design",
    "Code": "code",
    "AcceptanceTest": "test_case",
    "Simulation": "test_case",
    "Context": "natural_language",
    "EnvironmentalAssumption": "natural_language",
    "FormalReview": "review",
    "SafetyAnalysis": "lifecycle_document",
    "Package": "package",
}


def prepare(source, output):
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    original = source / "V1-simplified.json"
    data = json.loads(original.read_text(encoding="utf-8"))
    artifacts, edges = {}, set()

    def visit(node, kind, parent=None, path=()):
        raw = {k: v for k, v in node.items() if k != "children"}
        # Equal IDs with unequal bodies are distinct occurrences, not silently overwritten.
        canonical = json.dumps([kind, raw], sort_keys=True, ensure_ascii=False)
        key = "safa-" + hashlib.sha256(canonical.encode()).hexdigest()[:24]
        title = node.get("name") or str(node["id"])
        body = title + ("\n\n" + node["description"] if node.get("description", "").strip() else "")
        if key not in artifacts:
            artifacts[key] = {
                "external_id": key,
                "kind": TYPES.get(kind, "lifecycle_document"),
                "revision": commit,
                "content": body,
                "locator": "V1-simplified.json#" + str(node["id"]),
                "structure": {
                    "original_id": str(node["id"]),
                    "original_type": kind,
                    "title": title,
                    "source_file": "V1-simplified.json",
                    "parent_ids": [],
                    "relations": [],
                    "tree_paths": [],
                    "content_status": "reference_only"
                    if kind in {"Code", "Package"}
                    else "full_text",
                    "source_record": raw,
                    "repository": REPOSITORY,
                    "commit": commit,
                    "relationship_semantics": (
                        "SAFA artifact tree: dataset-provided trace relationships, "
                        "not filesystem containment"
                    ),
                },
            }
        current = artifacts[key]
        record_path = list(path) + [key]
        if record_path not in current["structure"]["tree_paths"]:
            current["structure"]["tree_paths"].append(record_path)
        if parent:
            if parent not in current["structure"]["parent_ids"]:
                current["structure"]["parent_ids"].append(parent)
            edges.add((parent, key, kind))
        for child_type, rows in node.get("children", {}).items():
            for row in rows:
                visit(row["DATA"], child_type, key, tuple(record_path))
        return key

    for tree in data["trees"].values():
        visit(tree["root-node"], "Hazard")
    for source_id, target_id, kind in sorted(edges):
        artifacts[source_id]["structure"]["relations"].append(
            {"target_id": target_id, "relation": "dataset_trace", "source_relation_type": kind}
        )
    provenance = {
        "repository": REPOSITORY,
        "commit": commit,
        "version": "V1",
        "source_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "license_file": "LICENSE",
        "source_path": str(original.resolve()),
        "limitations": [
            "Code nodes contain paths only; excluded from model analysis",
            "Some upstream descriptions are placeholders",
            "Tree links are independent reference data, never model input",
        ],
    }
    dataset = DatasetInput(
        tenant_id="local",
        project_id="dronology-safa",
        version="SAFA-V1-" + commit[:12],
        provenance=provenance,
        artifacts=list(artifacts.values()),
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "dataset.json").write_text(dataset.model_dump_json(indent=2), encoding="utf-8")
    report = {
        **provenance,
        "artifact_count": len(artifacts),
        "relation_count": len(edges),
        "kinds": dict(Counter(a["kind"] for a in artifacts.values())),
        "original_types": dict(
            Counter(a["structure"]["original_type"] for a in artifacts.values())
        ),
        "reference_only": sum(
            a["structure"]["content_status"] == "reference_only" for a in artifacts.values()
        ),
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "reference-links.json").write_text(
        json.dumps(
            [{"source_id": s, "target_id": t, "type": k} for s, t, k in sorted(edges)], indent=2
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT.parent / "software-quality-agent/datasets/SAFA-Artifacts",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/tlr/safa")
    args = parser.parse_args()
    prepare(args.source, args.output)
