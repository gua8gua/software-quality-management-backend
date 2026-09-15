from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Any
from .service import NormalizedArtifact, NormalizedDataset, NormalizedTrace


class SafaAdapter:
    name = "safa"
    def load(self, path: Path) -> NormalizedDataset:
        raw = json.loads(path.read_text(encoding="utf-8-sig")); version = str(raw.get("version") or path.stem)
        artifacts: dict[str, NormalizedArtifact] = {}; traces: list[NormalizedTrace] = []
        def visit(node: Any, parent: str | None = None, relation: str = "tree"):
            if not isinstance(node, dict): return
            if "DATA" in node: node = node["DATA"]
            if not node.get("id") and "root-node" in node:
                visit(node["root-node"], parent, "hazard"); return
            ident = node.get("id")
            if ident:
                ident = str(ident); typ = node.get("type") or node.get("kind") or relation
                if typ in {"tree", "root-node"}: typ = "hazard"
                desc = node.get("description") or node.get("name") or ""
                path_value = next((node.get(k) for k in ("path", "source_file", "file", "locator")
                                   if isinstance(node.get(k), str) and node.get(k)), None)
                if not path_value and str(typ).lower() in {"code", "package"} and isinstance(desc, str) and ("/" in desc or "\\" in desc):
                    path_value = desc
                artifacts.setdefault(ident, NormalizedArtifact(ident, str(typ), None if path_value else str(desc),
                    f"{path.name}#{ident}", f"{path.name}#{ident}", {"source_record": node},
                    "path" if path_value else "inline", path_value))
                if parent: traces.append(NormalizedTrace(parent, ident, relation, {"relation": relation}))
                parent = ident
            children = node.get("children")
            if isinstance(children, dict):
                for rel, values in children.items():
                    if isinstance(values, list):
                        for child in values: visit(child, parent, str(rel))
                    else: visit(values, parent, str(rel))
            elif not ident:
                for value in node.values():
                    visit(value, parent, relation)
        for tree in (raw.get("trees") or {}).values(): visit(tree)
        return NormalizedDataset("safa", version, "dronology", "Dronology",
            {"source_dataset": "SAFA-Artifacts", "source_version": version, "source_file": str(path),
             "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "repository": "https://github.com/SAREC-Lab/SAFA-Artifacts"}, list(artifacts.values()), traces)
