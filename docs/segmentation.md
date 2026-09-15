# Segmentation audit and selection

## Current call paths

- TLR calls `tlr.service._pipeline` → `preprocess_typed` → `preprocess_sync`.
  `RunOptions.source_preprocessor`, `target_preprocessor`, and `kind_preprocessors` select
  `artifact`, `chunk`, `sentence`, `sections`, `method`, `class`, `model_features`, or
  `llm_structure`. The typed splitter preserves character offsets into the original content;
  Java/Python method/class modes preserve code declaration boundaries and leading comments.
- LiSSA is instantiated only when `retrieval_backend=lissa`. `LissaRetriever` receives already
  created `TlrElement` embeddings and returns candidate pairs. It does not receive documents
  and does not perform segmentation.
- TLR requirement hierarchy processing can replace source preprocessing with
  `requirements.processing.requirement_elements`; this is an intentional task-specific path.
- Consistency analysis consumes existing TLR elements and full artifact/context offsets; it does
  not call LiSSA or the document index splitter itself.
- General document indexing calls `PlainTextDocumentParser` then the configured splitter.
  The default remains `character` (overlap character windows); `paragraph` is now available.

## PDF facts

`tlr.uploads.extract_text` uses pypdf page-by-page `extract_text()`, joins pages with newlines,
and discards page boundaries, layout, tables, images and OCR. The resulting string then follows
the ordinary character splitter path. No OCR, layout-aware, table-aware or image-aware splitter
is currently implemented.

## Selection

The existing TLR per-role/per-kind options are the task-level selector. General document indexing
uses `DOCUMENT_SPLITTER=character|paragraph`. Every TLR run records the selected segmentation
configuration in its manifest, allowing later comparison without changing the LiSSA retrieval
implementation.
