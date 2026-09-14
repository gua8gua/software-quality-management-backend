"""Read-only layer projection of supplied paths/parents and explicit input hierarchy.

Do not reuse the legacy single-parent projection for multi-parent source records.
"""
import hashlib
import json

from app.modules.tlr.planning import LAYERS, layer_of


def original_structure(artifacts, hierarchy, layer):
    by_external = {a.external_id: a for a in artifacts}
    by_id = {a.id: a for a in artifacts}
    layers = [
        {"id": key, "label": label, "count": sum(a.kind != "package" and layer_of(a) == key for a in artifacts)}
        for key, label in LAYERS
    ]
    selected = {a.external_id for a in artifacts if a.kind != "package" and layer_of(a) == layer}
    nodes, warnings, covered = {}, set(), set()

    def add_path(path, source):
        parent, previous_index = None, -1
        for index, external in enumerate(path):
            a = by_external.get(external)
            if not a or (external not in selected and a.kind != "package"):
                continue
            prefix = path[:index + 1]
            key = "original:" + hashlib.sha256(json.dumps(prefix).encode()).hexdigest()[:24]
            nodes[key] = {
                "id": key, "node_key": key, "parent_id": parent, "artifact_id": a.id,
                "title": (a.structure or {}).get("title") or (a.structure or {}).get("original_id") or external,
                "node_type": (a.structure or {}).get("original_type") or a.kind,
                "metadata_json": {"source": source, "external_id": external,
                    "original_path": prefix, "omitted_ancestors": path[previous_index + 1:index],
                    "container": a.kind == "package", "content_status": (a.structure or {}).get("content_status")},
            }
            parent, previous_index = key, index
            covered.add(external)

    def parent_paths(external, visiting=()):
        if external in visiting:
            warnings.add("原始父节点关系存在循环，已在循环处停止展开。")
            return []
        a = by_external.get(external)
        parents = (a.structure or {}).get("parent_ids", []) if a else []
        if not parents:
            return [[external]]
        paths = []
        for parent in parents:
            if parent not in by_external:
                warnings.add("部分原始父节点不在当前快照中。")
            for path in parent_paths(parent, (*visiting, external)):
                paths.append([*path, external])
                if len(paths) >= 20000:
                    warnings.add("原始路径数量超过展示预算，已停止继续展开。")
                    return paths
        return paths

    for external in sorted(selected):
        structure = by_external[external].structure or {}
        paths = structure.get("tree_paths") or []
        if paths:
            for path in paths:
                if not isinstance(path, list) or not path or path[-1] != external:
                    warnings.add("部分原始路径与所属工件不一致，未作为层级使用。")
                    continue
                if len(set(path)) != len(path):
                    warnings.add("原始路径存在重复节点，未作为层级使用。")
                    continue
                add_path(path, "artifact.structure.tree_paths")
        elif structure.get("parent_ids"):
            for path in parent_paths(external):
                add_path(path, "artifact.structure.parent_ids")

    # Explicitly supplied input trees remain valid for imports without path metadata.
    explicit = {n.id: n for n in hierarchy if (n.metadata_json or {}).get("source") != "artifact.structure"}
    raw_covered = set(covered)
    for n in explicit.values():
        a = by_id.get(n.artifact_id)
        if not a or a.external_id not in selected or a.external_id in raw_covered:
            continue
        path, seen, current = [], set(), n
        while current and current.id not in seen:
            seen.add(current.id); path.append(current); current = explicit.get(current.parent_id)
        if current:
            warnings.add("输入层级存在循环，已停止展开。")
        parent, omitted = None, []
        for item in reversed(path):
            artifact = by_id.get(item.artifact_id)
            if artifact and artifact.external_id not in selected and artifact.kind != "package":
                omitted.append(item.node_key); continue
            key = "input:" + item.id
            nodes[key] = {"id": key, "node_key": item.node_key, "parent_id": parent,
                "artifact_id": item.artifact_id, "title": item.title, "node_type": item.node_type,
                "metadata_json": {"source": "input.hierarchy", "omitted_ancestors": omitted,
                    "container": not artifact or artifact.kind == "package"}}
            parent, omitted = key, []
            if artifact:
                covered.add(artifact.external_id)

    unstructured = selected - covered
    for external in sorted(unstructured):
        add_path([external], "no_original_parent")
    return {"layer": layer, "layers": layers, "nodes": list(nodes.values()),
        "artifact_count": len(selected), "unstructured_count": len(unstructured),
        "warnings": sorted(warnings),
        "semantics": sorted({(a.structure or {}).get("relationship_semantics") for a in artifacts
            if a.external_id in selected and (a.structure or {}).get("relationship_semantics")})}
