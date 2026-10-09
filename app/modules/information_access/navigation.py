"""Exact tree-position lookup over the existing canonical parent/ordinal tree.

Positions are not inferred chapter numbers. ``/2/4`` means the second child of
the document, then its fourth child (one-based sibling_ordinal). Heading names
are returned alongside them. No new document tree or persisted content is made.
"""

from collections import defaultdict

from app.modules.artifacts.models import ContentNode
from app.modules.information_access.service import InformationTools
from app.modules.traceability.projections import heading_path


def position_index(nodes: list[ContentNode]) -> dict[str, str]:
    by_id = {node.id: node for node in nodes}
    roots = [node for node in nodes if node.parent_id is None]
    if len(roots) != 1 or roots[0].node_type != "document":
        raise ValueError("canonical navigation requires one document root")
    children = defaultdict(list)
    for node in nodes:
        if node.parent_id is not None:
            if node.parent_id not in by_id:
                raise ValueError("canonical navigation has an orphan")
            children[node.parent_id].append(node)
    result = {roots[0].id: "/"}
    queue = [roots[0]]
    for parent in queue:
        siblings = children[parent.id]
        ordinals = [node.sibling_ordinal for node in siblings]
        if any(value is None or value < 0 for value in ordinals) or len(set(ordinals)) != len(ordinals):
            raise ValueError("canonical navigation requires unique nonnegative sibling ordinals")
        for child in sorted(siblings, key=lambda node: node.sibling_ordinal):
            if child.id in result:
                raise ValueError("canonical navigation contains a cycle")
            result[child.id] = result[parent.id].rstrip("/") + f"/{child.sibling_ordinal + 1}"
            queue.append(child)
    if len(result) != len(nodes):
        raise ValueError("canonical navigation contains disconnected nodes")
    return result


class StructureNavigation(InformationTools):
    async def _index(self, artifact_id: str):
        nodes = await self._nodes(artifact_id)
        return nodes, position_index(nodes)

    @staticmethod
    def _entry(node: ContentNode, positions: dict[str, str], nodes: dict[str, ContentNode]) -> dict:
        return {"node_id": node.id, "artifact_id": node.artifact_id,
                "position": positions[node.id], "title": node.title,
                "node_type": node.node_type, "heading_path": heading_path(node, nodes),
                "text_preview": node.text[:160] if node.node_type == "paragraph" else None}

    async def locate(self, node_id: str) -> dict:
        node = await self.node(node_id)
        nodes, positions = await self._index(node.artifact_id)
        return self._entry(node, positions, {item.id: item for item in nodes})

    async def resolve(self, artifact_id: str, position: str) -> dict:
        nodes, positions = await self._index(artifact_id)
        node = next((item for item in nodes if positions[item.id] == position), None)
        if node is None:
            raise ValueError("结构位置不存在；请从目录工具返回的 position 原样复制，不把章节编号当树位置")
        return self._entry(node, positions, {item.id: item for item in nodes})

    async def browse(self, artifact_id: str, position: str = "/", *,
                     offset: int = 0, limit: int = 50) -> dict:
        self._page(offset, limit)
        nodes, positions = await self._index(artifact_id)
        by_id = {node.id: node for node in nodes}
        root = next((node for node in nodes if positions[node.id] == position), None)
        if root is None:
            raise ValueError("结构位置不存在；先从上级目录取得 position")
        prefix = position.rstrip("/") + "/"
        headings = [node for node in nodes if node.id != root.id
                    and positions[node.id].startswith(prefix)
                    and node.node_type == "section"]
        children = sorted([node for node in nodes if node.parent_id == root.id],
                          key=lambda node: node.sibling_ordinal)
        # Independent cursors share an offset but disclose independent totals.
        # Every descendant title is reachable; depth never silently drops it.
        more = offset + limit < max(len(children), len(headings))
        return {"source": "canonical_parent_ordinal_tree",
                "current": self._entry(root, positions, by_id),
                "parent_position": positions.get(root.parent_id),
                "children": [self._entry(node, positions, by_id) for node in children[offset:offset + limit]],
                "descendant_headings": [self._entry(node, positions, by_id) for node in headings[offset:offset + limit]],
                "child_total": len(children), "heading_total": len(headings),
                "has_more": more, "next_offset": offset + limit if more else None,
                "position_rule": "position 是按真实父子关系与 sibling_ordinal 得到的树路径；标题中的 3.2.4 只是章节名，不可代替 position。"}
