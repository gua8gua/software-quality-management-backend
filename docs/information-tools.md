# Information and review tool contracts

All routes are project-scoped under `/api/v1/projects/{project_id}` with a
`tenant_id` query parameter (default `local`). The Python `InformationTools`
class is the read-only business interface; `ReviewTools` is the separate
business-state interface. Neither is registered with an Agent SDK in this
change. The HTTP routes are explicit business methods, not arbitrary SQL.

Read-only `/information` routes:

| Tool path | Content |
| --- | --- |
| `GET /artifacts`, `/artifacts/{id}`, `/artifacts/{id}/status`, `/artifacts/{id}/relations` | Registry, original-file metadata, canonical counts, known missing provenance and artifact links derived from formal TraceLinks. Parse-failure regions are not recorded by the current schema. |
| `GET /artifacts/{id}/outline`, `/level/{depth}` | Paged structural overview or a selected tree level. |
| `GET /nodes/{id}/subtree`, `/path`, `/neighborhood` | Paged descendants, root-to-node path, parent/children/siblings/reading neighbors. |
| `GET /nodes/{id}` | Canonical text range, type, heading path, Docling references, provenance, optional bounded raw-structure JSON slice (`structure_start`/`structure_limit`). |
| `GET /nodes/{id}/table-cells` | Paged original Docling table cells and row/column counts. |
| `GET /artifacts/{id}/read` | Paged reading-order content, optionally filtered by page. Repeat until `has_more=false` to read the whole parsed document. |
| `GET /nodes/{id}/relations`, `/relations/between` | Explicit canonical relationships and unresolved references. |
| `GET /tlr-runs`, `/tlr-runs/{id}` | Run history/config/status, annotation versions and paged candidate failures. |
| `GET /tlr-runs/{id}/annotations` | Model-derived role/synopsis with provenance of model/prompt and legacy flag. Supports node/section/role/failure filters. |
| `GET /tlr-runs/{id}/nodes/{node_id}/results`, `/links`, `/mandatory` | Candidate diagnostics, direct links, mandatory review leads. |
| `GET /nodes/{id}/bm25-terms`, `/similar-bm25` | Actual jieba `cut_for_search` terms and frequency; an on-demand BM25 investigation search. IDF and per-term contribution are **not** exposed. |
| `GET /artifacts/{id}/search` | Scoped BM25, exact text or heading search across canonical nodes, including nodes outside TLR units. |
| `POST /tlr-runs/{id}/search-dense` | Embeds a supplied query and searches only existing raw TLR embeddings; does not create another index or TraceLink. |

BM25 investigation queries use the existing `ChineseTokenizer` and
`bm25s.BM25(method="lucene")` setup. Search results are investigation leads,
never formal TLR output. Full original binary files are represented by file
metadata and canonical text/Docling provenance; this layer does not send a
binary PDF/DOCX to the model. `node_detail(include_structure=true)` is explicit
and bounded; use `structure_start`/`structure_limit` or `table-cells` for
continued inspection.

The mandatory set is the union of successfully judged `direct`/`related`
candidate nodes and formal RRF seeds with `fused_rank <= run.options.fine_top_k`.
Failed candidates inside that rank bound remain visible. This is a review
checklist, **not** a new retrieval verdict or a claim of optimal recall. The
review tools do not limit exploration to this set.

Write-state `/reviews` routes create a review, register exact source sentence
ranges, register checks, save exact design quotes with offsets, update check
status, mark mandatory nodes visited, and close sentences/reviews. The review
state response pages sentences and mandatory IDs independently. Formal
review facts are separate from future conversation history. A `completed`
review means the registered sentences/checks and mandatory checklist were
closed; it does not assert that an LLM analysis is correct or exhaustive.

Legacy requirement-evaluation tables are intentionally removed by migration
`0009_review_tools`. The migration is destructive and does not erase canonical
nodes or formal TLR tables. No Agent SDK loop, Session, handoff, or local-model
tool-calling compatibility is claimed here.
