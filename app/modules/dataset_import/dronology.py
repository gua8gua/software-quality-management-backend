from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Any
from .service import NormalizedArtifact, NormalizedDataset, NormalizedTrace


def _text(value: Any) -> str:
    if isinstance(value, str): return value
    if value is None: return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _walk(value: Any, found: list[dict], traces: list[NormalizedTrace], parent: str | None = None):
    if isinstance(value, dict):
        ident = next((value.get(k) for k in ("id", "identifier", "key", "external_id") if value.get(k)), None)
        typ = next((value.get(k) for k in ("type", "kind", "artifact_type", "artifactType") if value.get(k)), None)
        if ident and typ:
            body = next((value.get(k) for k in ("content", "text", "body", "description", "statement", "name") if value.get(k)), "")
            found.append({"id": str(ident), "type": str(typ), "body": _text(body), "raw": value})
            if parent: traces.append(NormalizedTrace(parent, str(ident), "parent", {"parent": parent}))
            parent = str(ident)
        for key, child in value.items():
            low = key.lower()
            if any(x in low for x in ("trace", "link", "relation", "parent", "target")) and isinstance(child, list):
                for edge in child:
                    if isinstance(edge, dict):
                        source = edge.get("source") or edge.get("from") or edge.get("source_id")
                        target = edge.get("target") or edge.get("to") or edge.get("target_id")
                        if source and target: traces.append(NormalizedTrace(str(source), str(target), str(edge.get("relation") or key), edge))
            _walk(child, found, traces, parent)
    elif isinstance(value, list):
        for child in value: _walk(child, found, traces, parent)


class DronologyDataset01Adapter:
    name = "dronology-dataset01"
    def load(self, path: Path) -> NormalizedDataset:
        raw = json.loads(path.read_text(encoding="utf-8-sig")); entries = raw.get("entries", [])
        artifacts: list[NormalizedArtifact] = []; traces: list[NormalizedTrace] = []
        for entry in entries:
            ident = str(entry.get("issueid")); attrs = entry.get("attributes", {}); typ = str(attrs.get("issuetype", "unknown"))
            artifacts.append(NormalizedArtifact(ident, typ, str(attrs.get("description") or attrs.get("summary") or ""),
                f"{path.name}#{ident}", f"{path.name}#{ident}", {"summary": attrs.get("summary"), "status": attrs.get("status"), "source_record": entry}))
            for relation, targets in (entry.get("children") or {}).items():
                for target in targets or []: traces.append(NormalizedTrace(ident, str(target), relation, {"relation": relation}))
            for index, code in enumerate(entry.get("code") or []):
                filename = str(code.get("filename") or f"{ident}-code-{index}"); code_id = f"{ident}::code::{index}"
                artifacts.append(NormalizedArtifact(code_id, "source code", None, f"{path.name}#{code_id}", filename,
                    {"status": code.get("status"), "timestamp": code.get("timestamp"), "source_record": code},
                    "path", filename))
                traces.append(NormalizedTrace(code_id, ident, "code_trace", {"filename": filename, **code}))
        return NormalizedDataset("dronology-dataset01", "dataset-01", "dronology", "Dronology",
            {"source_dataset": "dronology-dataset01", "source_version": "dataset-01", "source_url": "https://sarec.nd.edu/dronology/datasets/01/dronologydataset01.json", "source_file": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, artifacts, traces)
