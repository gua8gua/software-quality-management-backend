"""Docling conversion boundary.

The converter returns the native ``DoclingDocument``.  Normalization and all
project-owned structure decisions belong to ``modules.segmentation``.
"""

from io import BytesIO
from pathlib import PurePath


def convert_bytes(filename: str, data: bytes):
    try:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import HeadingHierarchyOptions, PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise RuntimeError(
            "Docling is required for PDF/DOCX conversion; install the document dependencies"
        ) from exc

    suffix = PurePath(filename).suffix.lower()
    formats = {".pdf": InputFormat.PDF, ".docx": InputFormat.DOCX}
    if suffix not in formats:
        raise ValueError(f"Docling document parser does not support {suffix or 'unknown'}")

    format_options = None
    if suffix == ".pdf":
        # This is Docling's maintained hierarchy stage.  No project-owned PDF
        # font/geometry heading detector is introduced here.  Picture
        # description/classification remain disabled (their defaults).
        pipeline = PdfPipelineOptions(
            heading_hierarchy_options=HeadingHierarchyOptions(enabled=True),
            generate_parsed_pages=True,
            do_picture_description=False,
            do_picture_classification=False,
        )
        format_options = {InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}

    converter = DocumentConverter(
        allowed_formats=[formats[suffix]],
        format_options=format_options,
    )
    stream = DocumentStream(name=filename, stream=BytesIO(data))
    result = converter.convert(stream)
    return result.document, result
