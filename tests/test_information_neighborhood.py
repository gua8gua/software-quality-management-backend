"""The model-facing neighborhood must distinguish partial pages from EOF."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.information_access.service import InformationTools


def node(identifier, parent=None, ordinal=0):
    return SimpleNamespace(
        id=identifier, artifact_id="artifact", parent_id=parent,
        node_type="paragraph", original_type="text", title="",
        reading_order=ordinal, sibling_ordinal=ordinal,
        provenance_json=[], docling_ref=f"#/texts/{identifier}",
    )


class NeighborhoodTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, root, nodes):
        service = InformationTools(None, "project")
        service.node = AsyncMock(return_value=root)
        service._nodes = AsyncMock(return_value=nodes)
        service.node_relations = AsyncMock(return_value={
            "items": [], "has_more": False, "next_offset": 100})
        return service

    async def test_children_can_be_read_beyond_a_page_without_enlarging_limit(self):
        root = node("root")
        children = [node(f"child-{i}", "root", i) for i in range(237)]
        service = self.make_service(root, [root, *reversed(children)])
        offset, identifiers = 0, []
        while True:
            page = await service.neighborhood("root", child_limit=17, child_offset=offset)
            self.assertEqual(page["children_total"], len(children))
            self.assertLessEqual(len(page["children"]), 17)
            identifiers.extend(item["node_id"] for item in page["children"])
            if not page["children_has_more"]:
                self.assertIsNone(page["children_next_offset"])
                break
            self.assertGreater(page["children_next_offset"], offset)
            offset = page["children_next_offset"]
        self.assertEqual(identifiers, [child.id for child in children])
        self.assertFalse(page["relations_has_more"])
        self.assertIsNone(page["relations_next_offset"])

    async def test_omitted_list_is_not_reported_as_empty_complete_list(self):
        root = node("root")
        service = self.make_service(root, [root, node("child", "root")])
        page = await service.neighborhood("root", child_limit=0)
        self.assertEqual(page["children"], [])
        self.assertEqual(page["children_total"], 1)
        self.assertTrue(page["children_has_more"])
        self.assertIsNone(page["children_next_offset"])
        page = await service.neighborhood("root", child_limit=10)
        self.assertFalse(page["children_has_more"])
        self.assertIsNone(page["children_next_offset"])
        leaf = self.make_service(node("leaf"), [node("leaf")])
        page = await leaf.neighborhood("leaf")
        self.assertEqual(page["children_total"], 0)
        self.assertFalse(page["children_has_more"])

    async def test_sibling_order_and_offsets_are_stable(self):
        parent = node("parent")
        root = node("current", "parent", 2)
        siblings = [node("previous", "parent", 1), node("next", "parent", 3)]
        service = self.make_service(root, [parent, root, *reversed(siblings)])
        first = await service.neighborhood("current", sibling_limit=1)
        second = await service.neighborhood("current", sibling_limit=1, sibling_offset=1)
        self.assertEqual(first["siblings"][0]["node_id"], "previous")
        self.assertEqual(second["siblings"][0]["node_id"], "next")
        self.assertEqual(first["siblings_next_offset"], 1)
        self.assertFalse(second["siblings_has_more"])
        self.assertIsNone(second["siblings_next_offset"])
        with self.assertRaises(ValueError):
            await service.neighborhood("current", child_offset=-1)


if __name__ == "__main__":
    unittest.main()
