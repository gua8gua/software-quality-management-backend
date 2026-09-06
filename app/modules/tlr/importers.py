"""TraceLab XML collection adapter, used by LiSSA SMOS. Never treats a path as content."""

import csv
import hashlib
from pathlib import Path
from xml.etree import ElementTree

from app.modules.tlr.schemas import ArtifactInput, GoldLink


def read_collection(xml_path: Path, kind: str, revision: str) -> list[ArtifactInput]:
    raw = xml_path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("DTD/entity declarations are not accepted")
    root = ElementTree.fromstring(raw)
    location = root.findtext("collection_info/content_location")
    if location not in {"internal", "external"}:
        raise ValueError("Unknown collection content_location")
    base = xml_path.parent.resolve()
    artifacts = []
    for node in root.findall("artifacts/artifact"):
        external_id = node.findtext("id")
        content = node.findtext("content")
        if not external_id or not content:
            raise ValueError("Artifact missing identifier/content")
        locator = f"{xml_path.name}#{external_id}"
        if location == "external":
            target = (base / content).resolve()
            if not target.is_relative_to(base):
                raise ValueError("External artifact escapes dataset directory")
            if not target.is_file():
                raise ValueError(f"Missing artifact body: {content}; download original dataset")
            content = target.read_text(encoding="utf-8-sig")
            locator = target.relative_to(base).as_posix()
        artifacts.append(
            ArtifactInput(
                external_id=external_id,
                kind=kind,
                revision=revision,
                content=content,
                locator=locator,
                structure={
                    "source_file": xml_path.name,
                    "path": locator,
                    "content_location": location,
                    "content_status": "full_text",
                    "parent_ids": [],
                    "relations": [],
                },
            )
        )
    if not artifacts or len({a.external_id for a in artifacts}) != len(artifacts):
        raise ValueError("Empty collection or duplicate identifiers")
    return artifacts


def read_gold(csv_path: Path) -> list[GoldLink]:
    # Upstream answer.csv has no header; do not drop its first link.
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        result = []
        for row in csv.reader(stream):
            if len(row) != 2:
                raise ValueError("Expected two-column headerless gold standard CSV")
            result.append(GoldLink(source_id=row[0].strip(), target_id=row[1].strip()))
    return result


def file_provenance(path: Path) -> dict:
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
