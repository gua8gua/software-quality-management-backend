"""Compare persisted before/after canonical parent paths on the real SRS/SDS."""

from __future__ import annotations

import json
from pathlib import Path

CATEGORIES = {
    "SRS": {
        "用例": "3.2 ",
        "功能需求": "3.3 ",
        "性能需求": "3.4 ",
        "数据需求": "3.7 ",
        "安全需求": "3.9 ",
        "设计实现约束": "3.10 ",
        "质量属性": "3.12.1 ",
    },
    "SDS": {
        "系统总体设计": "4 ",
        "详细设计": "5 ",
        "数据库设计": "6 ",
        "页面设计": "7 ",
        "第3章具体技术方案": "3.3.3",
    },
}


def _path(nodes: list[dict], index: int) -> list[str]:
    result: list[str] = []
    seen: set[int] = set()
    current: int | None = index
    while current is not None and current not in seen:
        seen.add(current)
        node = nodes[current]
        if node["canonical_type"] in {"document", "section"} and node["title"].strip():
            result.append(node["title"].strip())
        current = node["parent_index"]
    return list(reversed(result))


def _load(directory: Path) -> dict[str, dict]:
    result = {}
    for path in directory.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        kind = "SRS" if payload["validation"]["canonical_types"]["paragraph"] < 1000 else "SDS"
        result[kind] = payload
    return result


def _display_content(node: dict) -> str:
    if node["canonical_type"] == "paragraph":
        return node["text"].strip()
    cells = (
        node.get("content_data", {})
        .get("docling_item", {})
        .get("data", {})
        .get("table_cells", [])
    )
    return " | ".join(str(cell.get("text", "")).strip() for cell in cells if cell.get("text"))


def compare(before_dir: Path, after_dir: Path) -> list[dict]:
    before_docs = _load(before_dir)
    after_docs = _load(after_dir)
    cases: list[dict] = []
    for kind, categories in CATEGORIES.items():
        before = before_docs[kind]["nodes"]
        after = after_docs[kind]["nodes"]
        old_by_ref = {node["docling_ref"]: node for node in before if node["docling_ref"]}
        for category, anchor in categories.items():
            selected = []
            for node in after:
                content = _display_content(node)
                if node["canonical_type"] not in {"paragraph", "table"} or not content:
                    continue
                new_path = _path(after, node["index"])
                if not any(title.startswith(anchor) for title in new_path):
                    continue
                old = old_by_ref.get(node["docling_ref"])
                if old is None:
                    continue
                old_path = _path(before, old["index"])
                if old_path == new_path:
                    continue
                if category == "设计实现约束" and selected and "CO-6" not in content:
                    continue
                selected.append(
                    {
                        "document": kind,
                        "category": category,
                        "docling_ref": node["docling_ref"],
                        "canonical_type": node["canonical_type"],
                        "original_type": node["docling_original_type"],
                        "text": content.replace("\n", " ")[:240],
                        "old_parent_index": old["parent_index"],
                        "new_parent_index": node["parent_index"],
                        "docling_parent_ref": node["docling_parent_ref"],
                        "heading_level": node["heading_level"],
                        "sibling_ordinal": node["sibling_ordinal"],
                        "hierarchy_status": node["hierarchy_status"],
                        "hierarchy_source": node["hierarchy_source"],
                        "old_heading_path": old_path,
                        "new_heading_path": new_path,
                    }
                )
                if len(selected) == 2:
                    break
            if not selected:
                raise RuntimeError(f"not enough changed cases for {kind}/{category}")
            cases.extend(selected)
    if len(cases) < 20:
        raise RuntimeError(f"expected at least 20 changed cases, got {len(cases)}")
    return cases


def main() -> None:
    root = Path("outputs")
    cases = compare(
        root / "segmentation-before-heading-fix",
        root / "segmentation-after-heading-fix",
    )
    output = root / "traceability"
    output.mkdir(parents=True, exist_ok=True)
    (output / "heading-path-before-after.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# Heading path before/after", "", f"Cases: {len(cases)}", ""]
    for index, case in enumerate(cases, start=1):
        lines.extend(
            [
                f"## {index}. {case['document']} / {case['category']}",
                "",
                f"- 原文：{case['text']}",
                f"- Docling ref / parent：`{case['docling_ref']}` / `{case['docling_parent_ref']}`",
                f"- old：{' > '.join(case['old_heading_path'])}",
                f"- new：{' > '.join(case['new_heading_path'])}",
                f"- hierarchy：{case['hierarchy_status']} / {case['hierarchy_source']}",
                "",
            ]
        )
    (output / "heading-path-before-after.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"cases": len(cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
