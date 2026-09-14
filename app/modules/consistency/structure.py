"""Syntax containment only; never infer requirement decomposition or resolved calls."""

import ast
import hashlib
import json
import re
from pathlib import PurePosixPath


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def artifact_fingerprint(artifact):
    return digest(
        json.dumps(
            {
                "content": artifact.content,
                "kind": artifact.kind,
                "locator": artifact.locator,
                "revision": artifact.revision,
                "structure": artifact.structure,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def code_structure(artifact):
    if (artifact.structure or {}).get("content_status") == "reference_only":
        return {
            "nodes": [],
            "warnings": ["reference_only: source body unavailable"],
            "semantics": "no_source_body",
        }
    text = artifact.content
    nodes = []
    warnings = []
    suffix = PurePosixPath(artifact.locator).suffix.lower()
    language = (artifact.structure or {}).get("language")
    if language in {"python", "java"}:
        suffix = {"python": ".py", "java": ".java"}[language]
    root_id = artifact.external_id + ":file"
    nodes.append(
        dict(id=root_id, parent_id=None, kind="file", name=artifact.locator, start=0, end=len(text))
    )
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    try:
        if suffix == ".py":
            tree = ast.parse(text)

            def walk(node, parent):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = offsets[
                        min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                    ]
                    # AST col offsets are UTF-8 bytes; line bounds avoid mixing units.
                    end = offsets[node.end_lineno]
                    identifier = f"{artifact.external_id}:{start}:{node.name}"
                    nodes.append(
                        dict(
                            id=identifier,
                            parent_id=parent,
                            kind="class" if isinstance(node, ast.ClassDef) else "function",
                            name=node.name,
                            start=start,
                            end=end,
                        )
                    )
                    parent = identifier
                for child in ast.iter_child_nodes(node):
                    walk(child, parent)

            walk(tree, root_id)
        elif suffix == ".java":
            import tree_sitter_java
            from tree_sitter import Language, Parser

            raw = text.encode("utf-8")
            tree = Parser(Language(tree_sitter_java.language())).parse(raw)
            if tree.root_node.has_error:
                raise ValueError("Java syntax incomplete")
            types = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "enum_declaration": "enum",
                "record_declaration": "record",
                "method_declaration": "function",
                "constructor_declaration": "constructor",
            }

            def walk_java(node, parent):
                if node.type in types:
                    name_node = node.child_by_field_name("name")
                    name = (
                        raw[name_node.start_byte : name_node.end_byte].decode()
                        if name_node
                        else node.type
                    )
                    start = len(raw[: node.start_byte].decode())
                    end = len(raw[: node.end_byte].decode())
                    identifier = f"{artifact.external_id}:{start}:{name}"
                    nodes.append(
                        dict(
                            id=identifier,
                            parent_id=parent,
                            kind=types[node.type],
                            name=name,
                            start=start,
                            end=end,
                        )
                    )
                    parent = identifier
                for child in node.named_children:
                    walk_java(child, parent)

            walk_java(tree.root_node, root_id)
        else:
            warnings.append("unsupported_language: file-level evidence only")
    except (SyntaxError, ValueError, RecursionError) as exc:
        nodes = nodes[:1]
        warnings.append(f"parse_failed: {type(exc).__name__}; file-level evidence only")
    for node in nodes:
        node["start_line"] = text.count("\n", 0, node["start"]) + 1
        node["end_line"] = text.count("\n", 0, max(node["start"], node["end"] - 1)) + 1
    return {
        "nodes": nodes,
        "warnings": warnings,
        "semantics": "syntactic_containment; no resolved call graph",
    }


def document_sections(artifact):
    """Markdown headings preserve document containment, not semantic obligations."""
    nodes, stack = [], []
    matches = list(re.finditer(r"(?m)^(#{1,6})[ \t]+(.+)$", artifact.content))
    for i, match in enumerate(matches):
        level = len(match[1])
        while stack and stack[-1][0] >= level:
            stack.pop()
        end = next(
            (m.start() for m in matches[i + 1 :] if len(m[1]) <= level), len(artifact.content)
        )
        key = f"{artifact.external_id}:section:{match.start()}"
        nodes.append(
            dict(
                id=key,
                parent_id=stack[-1][1] if stack else None,
                kind="document_section",
                title=match[2],
                start=match.start(),
                end=end,
            )
        )
        stack.append((level, key))
    return nodes


def evidence(artifact, start, end, origin, symbol=None):
    return dict(
        id=f"{artifact.external_id}@{start}:{end}",
        artifact_id=artifact.external_id,
        locator=artifact.locator,
        revision=artifact.revision,
        sha256=digest(artifact.content),
        start=start,
        end=end,
        start_line=artifact.content.count("\n", 0, start) + 1,
        end_line=artifact.content.count("\n", 0, max(start, end - 1)) + 1,
        kind=artifact.kind,
        symbol=symbol,
        origin=origin,
        text=artifact.content[start:end],
    )


def collect(requirement, artifacts, linked_elements, extras, budget, context=None):
    """Collect exact TLR slices first, enclosing declarations second, then file context."""
    by_id = {a.id: a for a in artifacts}
    packets, omitted, seen = [], [], set()
    used = 0

    def add(artifact, start, end, origin, symbol=None):
        nonlocal used
        if (artifact.structure or {}).get("content_status") == "reference_only":
            omitted.append({"artifact_id": artifact.external_id, "reason": "reference_only"})
            return
        key = (artifact.id, start, end)
        if key in seen:
            return
        seen.add(key)
        if not (0 <= start < end <= len(artifact.content)):
            omitted.append({"artifact_id": artifact.external_id, "reason": "invalid_offsets"})
            return
        if any(
            e["artifact_id"] == artifact.external_id and e["start"] <= start and e["end"] >= end
            for e in packets
        ):
            return
        contained = [
            e
            for e in packets
            if e["artifact_id"] == artifact.external_id and start <= e["start"] and end >= e["end"]
        ]
        replaced_chars = sum(len(e["text"]) for e in contained)
        if used - replaced_chars + end - start > budget:
            omitted.append(
                {
                    "artifact_id": artifact.external_id,
                    "start": start,
                    "end": end,
                    "reason": "evidence_budget",
                }
            )
            return
        for entry in contained:
            packets.remove(entry)
        packets.append(evidence(artifact, start, end, origin, symbol))
        used += end - start - replaced_chars

    linked = []
    for element in linked_elements:
        artifact = by_id[element.artifact_id]
        if artifact.content[element.start : element.end] != element.content:
            omitted.append(
                {"artifact_id": artifact.external_id, "reason": "nonliteral_tlr_element"}
            )
            continue
        add(artifact, element.start, element.end, "tlr_related")
        linked.append((artifact, element))
    for artifact, element in linked:
        if context is not None:
            selected_context = context.around(artifact.external_id, element.start, element.end)
            for excerpt in selected_context["excerpts"]:
                related_artifact = context.artifacts[excerpt["artifact_id"]]
                add(related_artifact, excerpt["start"], excerpt["end"], excerpt["role"])
        if artifact.kind not in {"code", "test_code"}:
            add(artifact, 0, len(artifact.content), "related_document_context")
            continue
        structure = (
            context.data[artifact.external_id]["code"] if context else code_structure(artifact)
        )
        containing = [n for n in structure["nodes"] if n["start"] <= element.start < n["end"]]
        for node in sorted(containing, key=lambda n: n["end"] - n["start"]):
            add(artifact, node["start"], node["end"], "enclosing_code", node["name"])
    for artifact in extras:
        add(artifact, 0, len(artifact.content), "explicit_context")
    return dict(
        requirement=dict(
            id=requirement.external_id,
            kind=requirement.kind,
            text=requirement.content,
            locator=requirement.locator,
            revision=requirement.revision,
            sha256=digest(requirement.content),
            structure=requirement.structure,
        ),
        evidence=packets,
        omitted=omitted,
        budget=budget,
    )
