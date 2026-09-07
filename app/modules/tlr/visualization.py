"""Read-only hierarchy projection for TLR visualization.

This module never creates domain Links. It only changes the visible endpoints of
persisted real Links according to UI-owned collapse state.
"""

from collections import defaultdict


def project_hierarchy(nodes, links, source_artifact_ids, target_artifact_ids, collapsed=None):
    collapsed = collapsed or {"source": set(), "target": set()}
    rows = {
        row.id: {
            "id": row.id,
            "parent_id": row.parent_id,
            "artifact_id": row.artifact_id,
            "node_key": row.node_key,
            "title": row.title,
            "node_type": row.node_type,
            "ordinal": row.ordinal,
            "metadata": row.metadata_json,
        }
        for row in nodes
    }
    children = defaultdict(list)
    for row in rows.values():
        children[row["parent_id"]].append(row["id"])
    for values in children.values():
        values.sort(
            key=lambda identifier: (rows[identifier]["ordinal"], rows[identifier]["node_key"])
        )

    artifact_nodes = defaultdict(list)
    for row in rows.values():
        if row["artifact_id"]:
            artifact_nodes[row["artifact_id"]].append(row["id"])
    for values in artifact_nodes.values():
        values.sort(
            key=lambda identifier: (rows[identifier]["ordinal"], rows[identifier]["node_key"])
        )

    link_payload = [
        {
            "id": link.id,
            "source_artifact_id": link.source_artifact_id,
            "target_artifact_id": link.target_artifact_id,
            "relation": link.relation,
            "evidence_candidate_ids": link.evidence_candidate_ids,
        }
        for link in links
    ]
    side_artifacts = {"source": set(source_artifact_ids), "target": set(target_artifact_ids)}
    result_nodes = []
    visible_for_artifact = {}

    for role in ("source", "target"):
        included = set()
        canonical = {}
        for artifact_id in side_artifacts[role]:
            candidates = artifact_nodes.get(artifact_id, [])
            if not candidates:
                continue
            canonical[artifact_id] = candidates[0]
            current = candidates[0]
            while current and current not in included:
                included.add(current)
                current = rows[current]["parent_id"]

        collapsed_ids = {
            value.split(":", 1)[-1] if value.startswith(f"{role}:") else value
            for value in collapsed.get(role, set())
        }
        visible = set()

        def visit(
            identifier,
            included=included,
            visible=visible,
            collapsed_ids=collapsed_ids,
        ):
            if identifier not in included or identifier in visible:
                return
            visible.add(identifier)
            if identifier not in collapsed_ids:
                for child in children.get(identifier, []):
                    visit(child)

        for root in children.get(None, []):
            visit(root)
        # Tolerate imported forests and legacy cyclic metadata without recursing forever.
        for identifier in included:
            if identifier not in visible:
                visit(identifier)

        descendants = defaultdict(set)
        for artifact_id, leaf in canonical.items():
            current = leaf
            path_seen = set()
            while current in included and current not in path_seen:
                path_seen.add(current)
                descendants[current].add(artifact_id)
                current = rows[current]["parent_id"]

            current = leaf
            path_seen = set()
            while current in included and current not in path_seen:
                path_seen.add(current)
                # Starting from the leaf finds self, otherwise nearest visible ancestor.
                if current in visible:
                    visible_for_artifact[(role, artifact_id)] = current
                    break
                current = rows[current]["parent_id"]

        for identifier in sorted(
            visible, key=lambda item: (rows[item]["ordinal"], rows[item]["node_key"])
        ):
            row = rows[identifier]
            parent_id = row["parent_id"] if row["parent_id"] in visible else None
            ancestor, seen = parent_id, {identifier}
            while ancestor:
                if ancestor in seen:
                    parent_id = None
                    break
                seen.add(ancestor)
                ancestor = rows[ancestor]["parent_id"] if ancestor in rows else None
            result_nodes.append(
                {
                    **row,
                    "id": f"{role}:{identifier}",
                    "hierarchy_node_id": identifier,
                    "parent_id": f"{role}:{parent_id}" if parent_id else None,
                    "role": role,
                    "pure_structure": row["artifact_id"] is None,
                    "collapsed": identifier in collapsed_ids,
                    "has_children": any(
                        child in included for child in children.get(identifier, [])
                    ),
                    "descendant_artifact_count": len(descendants[identifier]),
                    "related_link_count": 0,
                }
            )

    grouped = {}
    for link in link_payload:
        source = visible_for_artifact.get(("source", link["source_artifact_id"]))
        target = visible_for_artifact.get(("target", link["target_artifact_id"]))
        if not source or not target:
            continue
        key = (f"source:{source}", f"target:{target}")
        grouped.setdefault(key, []).append(link)

    projected_links = []
    counts = defaultdict(int)
    for (source, target), underlying in grouped.items():
        counts[source] += len(underlying)
        counts[target] += len(underlying)
        projected_links.append(
            {
                "id": f"projected:{source}:{target}",
                "source": source,
                "target": target,
                "count": len(underlying),
                "underlying_link_ids": [link["id"] for link in underlying],
                "links": underlying,
            }
        )
    for node in result_nodes:
        node["related_link_count"] = counts[node["id"]]
    return {"nodes": result_nodes, "links": projected_links}
