from types import SimpleNamespace as Row

from app.modules.tlr.original_structure import original_structure


def artifact(key, kind="requirement", **structure):
    return Row(id=key, external_id=key, kind=kind, structure=structure)


def test_original_paths_preserve_occurrences_and_filter_other_layers():
    rows = [artifact("risk", "hazard"), artifact("r1"), artifact("r2"),
            artifact("child", tree_paths=[["risk", "r1", "child"], ["risk", "r2", "child"]]),
            artifact("design", "design")]
    # This converted single-parent row is deliberately wrong.
    legacy = Row(id="bad", parent_id=None, artifact_id="child", node_key="bad", title="Generated wrong parent", node_type="group", metadata_json={"source": "artifact.structure"})
    result = original_structure(rows, [legacy], "requirements")
    nodes = result["nodes"]
    children = [n for n in nodes if n["artifact_id"] == "child"]
    assert len(children) == 2
    assert len({n["parent_id"] for n in children}) == 2
    assert {n["artifact_id"] for n in nodes} == {"r1", "r2", "child"}
    assert all(n["title"] != "Generated wrong parent" for n in nodes)
    assert any(n["metadata_json"]["omitted_ancestors"] == ["risk"] for n in nodes)


def test_reference_code_and_original_containers_are_not_discarded():
    rows = [artifact("r"), artifact("pkg", "package"), artifact("code", "code", content_status="reference_only", tree_paths=[["r", "pkg", "code"]])]
    result = original_structure(rows, [], "implementation")
    assert result["artifact_count"] == 1
    assert {n["artifact_id"] for n in result["nodes"]} == {"pkg", "code"}
    assert next(n for n in result["nodes"] if n["artifact_id"] == "code")["metadata_json"]["content_status"] == "reference_only"


def test_all_original_parents_are_used_and_cycles_do_not_hang():
    rows = [artifact("a"), artifact("b"), artifact("child", parent_ids=["a", "b"])]
    result = original_structure(rows, [], "requirements")
    assert len([n for n in result["nodes"] if n["artifact_id"] == "child"]) == 2
    cyclic = original_structure([artifact("a", parent_ids=["b"]), artifact("b", parent_ids=["a"])], [], "requirements")
    assert cyclic["warnings"]
    assert cyclic["artifact_count"] == 2


def test_explicit_input_hierarchy_keeps_repeated_artifact_membership():
    rows = [artifact("a")]
    def node(key, parent=None, artifact_id=None):
        return Row(id=key, parent_id=parent, artifact_id=artifact_id, node_key=key, title=key, node_type="input", metadata_json={})
    result = original_structure(rows, [node("g1"), node("g2"), node("n1", "g1", "a"), node("n2", "g2", "a")], "requirements")
    assert len([n for n in result["nodes"] if n["artifact_id"] == "a"]) == 2
