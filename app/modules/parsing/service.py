import ast
import re
from pathlib import PurePath

from app.modules.parsing.models import ContentNodeDraft, ParsedContent
from app.providers.documents.docling import convert_bytes

CODE_SUFFIXES = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".java": "java",
    ".py": "python",
    ".cs": "csharp",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
}


def _finalize(nodes: list[ContentNodeDraft]) -> list[ContentNodeDraft]:
    children: dict[int | None, list[int]] = {}
    for index, node in enumerate(nodes):
        node.reading_order = index
        children.setdefault(node.parent_index, []).append(index)
    for indexes in children.values():
        for sibling_ordinal, index in enumerate(indexes):
            nodes[index].sibling_ordinal = sibling_ordinal
    return nodes


def _plain_text(text: str, suffix: str) -> ParsedContent:
    """Build a small canonical tree for non-Docling text inputs."""
    nodes = [
        ContentNodeDraft(
            node_type="document",
            original_type="document",
            title="document",
            hierarchy_source="parser:plain-text",
        )
    ]
    section_stack: list[tuple[int, int]] = []
    paragraph_lines: list[str] = []

    def flush() -> None:
        if not paragraph_lines:
            return
        value = "".join(paragraph_lines).strip()
        paragraph_lines.clear()
        if not value:
            return
        nodes.append(
            ContentNodeDraft(
                node_type="paragraph",
                original_type="text",
                text=value,
                parent_index=section_stack[-1][1] if section_stack else 0,
                hierarchy_source="parser:plain-text",
                metadata={"format": suffix.lstrip(".") or "text"},
            )
        )

    for line in text.splitlines(keepends=True):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
        if heading:
            flush()
            level = len(heading.group(1))
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            nodes.append(
                ContentNodeDraft(
                    node_type="section",
                    original_type="markdown_heading",
                    title=heading.group(2),
                    parent_index=section_stack[-1][1] if section_stack else 0,
                    heading_level=level,
                    hierarchy_source="parser:markdown-heading",
                    metadata={"format": suffix.lstrip(".") or "text"},
                )
            )
            section_stack.append((level, len(nodes) - 1))
        elif line.strip():
            paragraph_lines.append(line)
        else:
            flush()
    flush()
    if len(nodes) == 1 and text:
        nodes.append(
            ContentNodeDraft(
                node_type="paragraph",
                original_type="text",
                text=text,
                parent_index=0,
                hierarchy_source="parser:plain-text",
            )
        )
    return ParsedContent(
        modality="text",
        file_format=suffix.lstrip("."),
        language="",
        nodes=_finalize(nodes),
        metadata={"parser": "plain-text"},
    )


def _source_code(text: str, language: str, suffix: str) -> ParsedContent:
    """Validate source syntax, but keep one faithful source leaf.

    Code structure recovery is outside this document-segmentation refactor.  A
    single paragraph avoids overlapping class/function copies in the canonical
    tree while keeping TLR inputs available.
    """
    if language == "python":
        ast.parse(text)
        parser = "python-ast"
    elif language in {"c", "java", "csharp"}:
        import importlib

        from tree_sitter import Language, Parser

        package = {
            "c": "tree_sitter_c",
            "java": "tree_sitter_java",
            "csharp": "tree_sitter_c_sharp",
        }[language]
        grammar = importlib.import_module(package)
        tree = Parser(Language(grammar.language())).parse(text.encode("utf-8"))
        if tree.root_node.has_error:
            raise ValueError(f"{language} syntax could not be parsed")
        parser = f"tree-sitter-{language}"
    else:
        parser = "source-text"
    nodes = [
        ContentNodeDraft(
            node_type="document",
            original_type="file",
            title="file",
            hierarchy_source=parser,
        )
    ]
    if text:
        nodes.append(
            ContentNodeDraft(
                node_type="paragraph",
                original_type="source_code",
                text=text,
                parent_index=0,
                hierarchy_source=parser,
                metadata={"language": language},
            )
        )
    return ParsedContent(
        modality="code",
        file_format=suffix.lstrip("."),
        language=language,
        nodes=_finalize(nodes),
        metadata={"parser": parser, "syntax_valid": True},
    )


def _docling(data: bytes, filename: str, suffix: str) -> ParsedContent:
    document, conversion = convert_bytes(filename, data)
    status = str(getattr(getattr(conversion, "status", None), "value", "success"))
    return ParsedContent(
        modality="text",
        file_format=suffix.lstrip("."),
        language="",
        docling_document=document,
        metadata={
            "parser": "docling",
            "status": status,
            "docling_schema": str(getattr(document, "schema_name", "")),
            "docling_document_version": str(getattr(document, "version", "")),
        },
    )


def parse_file(
    filename: str, data: bytes, modality: str = "text", language: str = ""
) -> ParsedContent:
    suffix = PurePath(filename).suffix.lower()
    if suffix in {".pdf", ".docx"}:
        return _docling(data, filename, suffix)
    text = data.decode("utf-8-sig", errors="replace")
    detected = language.lower() or CODE_SUFFIXES.get(suffix, "")
    if modality == "code" or detected in {"c", "java", "python", "csharp"}:
        return _source_code(text, detected, suffix)
    return _plain_text(text, suffix)
