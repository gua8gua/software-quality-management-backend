"""Opt-in SCT-guided TLR: requirements owns semantics, TLR owns candidate routing."""

import math

from app.modules.tlr.pipeline import validate_vectors
from app.modules.tlr.preprocessing import element


def terminal_units(analysis, artifact_id):
    return [
        n
        for n in analysis["requirements"][artifact_id]["nodes"]
        if n["kind"] != "requirement" and not n.get("children")
    ]


def requirement_elements(artifact, analysis, run_id, options):
    item = analysis["requirements"][artifact.external_id]
    nodes = {n["id"]: n for n in item["nodes"]}
    output = []
    for node in terminal_units(analysis, artifact.external_id):
        ancestry, current = [], nodes.get(node["parent_id"])
        while current:
            ancestry.append(
                {
                    "id": current["id"],
                    "text": current["text"][:4000],
                    "truncated": len(current["text"]) > 4000,
                }
            )
            current = nodes.get(current["parent_id"])
        # Oversize indivisible nodes become retrieval windows, not subrequirements.
        for start in range(node["start"], node["end"], options.max_element_chars):
            end = min(node["end"], start + options.max_element_chars)
            output.append(
                element(
                    artifact,
                    "source",
                    run_id,
                    len(output),
                    start,
                    end,
                    artifact.content[start:end],
                    "requirement_hierarchy",
                    {
                        "unit_id": node["id"],
                        "unit_state": node["state"],
                        "categories": node["categories"],
                        "requirement_context": {
                            "conditions": node["conditions"],
                            "ancestors": ancestry,
                            "categories": node["categories"],
                            "normalized_proposal": node["normalized"],
                            "neighbor_before": artifact.content[max(0, start - 800) : start],
                            "neighbor_after": artifact.content[end : end + 800],
                        },
                        "window_only": end - start < node["end"] - node["start"],
                    },
                )
            )
    return output


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.hypot(*a) * math.hypot(*b))


class HierarchicalRetriever:
    name = "sct-embedding-routing-v1"

    def __init__(self, analysis, embedding, base, options):
        self.analysis, self.embedding, self.base, self.options = analysis, embedding, base, options
        self.stats = {}

    async def retrieve(self, sources, targets, top_k):
        categories = self.analysis["categories"]["categories"]
        full = len(sources) * len(targets)
        if not categories:
            self.stats = {
                "routing": "flat_fallback",
                "reason": "no_reliable_categories",
                "full_pairs": full,
                "evaluated_pairs": full,
            }
            return await self.base.retrieve(sources, targets, top_k)
        descriptors, keys = [], []
        for cat in categories:
            keys.append((cat["key"], None))
            descriptors.append(cat["title"] + "\n" + cat["summary"])
            for sub in cat["subcategories"]:
                keys.append((cat["key"], sub["key"]))
                descriptors.append(cat["title"] + "\n" + sub["title"] + "\n" + sub["summary"])
        try:
            vectors = await self.embedding.embed(descriptors)
            validate_vectors(vectors, len(descriptors), len(sources[0].embedding))
        except Exception as exc:
            self.stats = {
                "routing": "flat_fallback",
                "reason": type(exc).__name__,
                "full_pairs": full,
                "evaluated_pairs": full,
            }
            return await self.base.retrieve(sources, targets, top_k)
        embeddings = dict(zip(keys, vectors, strict=True))
        routed = {}
        category_comparisons = 0
        for target in targets:
            category_comparisons += len(categories)
            high = sorted(
                ((cosine(target.embedding, embeddings[(c["key"], None)]), c) for c in categories),
                key=lambda v: -v[0],
            )
            if high[0][0] < self.options.hierarchy_min_similarity:
                routed[target.id] = None
                continue
            selected = high[: self.options.hierarchy_top_categories]
            memberships = set()
            for _, cat in selected:
                category_comparisons += len(cat["subcategories"])
                subs = sorted(
                    (
                        (cosine(target.embedding, embeddings[(cat["key"], s["key"])]), s)
                        for s in cat["subcategories"]
                    ),
                    key=lambda v: -v[0],
                )
                if not subs or subs[0][0] < self.options.hierarchy_min_similarity:
                    memberships = None
                    break
                for _, sub in subs[: self.options.hierarchy_top_subcategories]:
                    memberships.add((cat["key"], sub["key"]))
            routed[target.id] = memberships
        rows, comparisons, fallbacks = [], 0, 0
        groups = {}
        target_order = {t.id: i for i, t in enumerate(targets)}
        for source in sources:
            memberships = {
                (c["category"], c["subcategory"]) for c in source.processing.get("categories", [])
            }
            uncertain = source.processing.get("unit_state") in {
                "ambiguous",
                "analysis_error",
                "unresolved",
                "budget_fallback",
            }
            selected = [
                t
                for t in targets
                if not memberships
                or uncertain
                or routed[t.id] is None
                or memberships.intersection(routed[t.id])
            ]
            if not selected:
                selected = targets
                fallbacks += 1
            # Small deterministic out-of-cluster exploration, explicitly not exhaustive recall.
            for t in targets[
                :: max(1, len(targets) // max(1, self.options.hierarchy_probe_targets))
            ][: self.options.hierarchy_probe_targets]:
                if t not in selected:
                    selected.append(t)
            selected.sort(key=lambda t: target_order[t.id])
            comparisons += len(selected)
            key = tuple(t.id for t in selected)
            group = groups.setdefault(key, {"sources": [], "targets": selected})
            group["sources"].append(source)
        for group in groups.values():
            rows.extend(await self.base.retrieve(group["sources"], group["targets"], top_k))
        self.stats = {
            "routing": self.name,
            "full_pairs": full,
            "evaluated_pairs": comparisons,
            "pair_reduction": 1 - comparisons / full if full else 0,
            "category_comparisons": category_comparisons,
            "fallback_sources": fallbacks,
            "candidate_pairs": len(rows),
            "flat_candidate_pairs": len(sources) * min(top_k, len(targets)),
            "comparison_basis": "same_leaf_units_and_code_elements",
            "scope_complete": comparisons == full,
            "routes": {
                k: sorted(v) if v is not None else "global_fallback" for k, v in routed.items()
            },
            "category_embeddings": [{"key": list(k), "vector": v} for k, v in embeddings.items()],
        }
        return rows
