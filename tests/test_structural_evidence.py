from app.modules.artifacts.models import Artifact, ContentNode
from app.modules.traceability.evidence import assemble_evidence, structural_expand
from app.modules.traceability.models import TraceUnit
from app.modules.traceability.retrievers import HybridCandidate


def test_structural_sibling_is_independent_candidate_and_separate_support_context():
    parent = ContentNode(
        id="parent",
        artifact_id="design",
        node_type="section",
        original_type="section_header",
        title="Design",
        sibling_ordinal=0,
    )
    first = ContentNode(
        id="first",
        artifact_id="design",
        parent_id="parent",
        node_type="paragraph",
        original_type="text",
        text="candidate A",
        sibling_ordinal=0,
    )
    sibling = ContentNode(
        id="sibling",
        artifact_id="design",
        parent_id="parent",
        node_type="paragraph",
        original_type="text",
        text="candidate B implementation",
        sibling_ordinal=1,
    )
    source_node = ContentNode(
        id="source-node",
        artifact_id="requirement",
        node_type="paragraph",
        original_type="text",
        text="requirement",
    )
    source = TraceUnit(id="source", node_id=source_node.id, ordinal=0)
    first_unit = TraceUnit(id="first-unit", node_id=first.id, ordinal=1)
    sibling_unit = TraceUnit(id="sibling-unit", node_id=sibling.id, ordinal=2)
    seed = HybridCandidate(source=source, target=first_unit, rrf_score=1 / 61, fused_rank=1)
    nodes = {node.id: node for node in (parent, first, sibling, source_node)}

    expanded = structural_expand(
        [seed], [first_unit, sibling_unit], nodes, [], sibling_window=1
    )
    sibling_candidate = next(item for item in expanded if item.target.id == sibling_unit.id)
    assert sibling_candidate.retrieval_sources == {"structure"}
    assert sibling_candidate.target.node_id == sibling.id
    assert sibling_candidate.structural_reasons[0]["origin_node_id"] == first.id

    package = assemble_evidence(
        source_node,
        first,
        Artifact(id="requirement", project_id="p", external_id="r", engineering_role="requirement"),
        Artifact(id="design", project_id="p", external_id="d", engineering_role="design"),
        nodes,
        [],
        sibling_window=1,
    )
    support = next(item for item in package.supporting_context if item.node_id == sibling.id)
    assert package.candidate_design_node["node_id"] == first.id
    assert support.origin_node_id == first.id
    assert support.relation_type == "sibling"
