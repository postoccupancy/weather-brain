# Idempotent LlamaIndex literature ingestion

`ingest_literature_with_llamaindex()` still ingests the PDF corpus and configured
web documents. It now replaces only the sources successfully prepared for that
invocation, instead of appending another generation of their chunks.

Source identity remains the existing `metadata.source` (PDF filename or web URL).
PDF filenames must be distinct within a batch. Strict PDF loading aborts if any
requested file fails or does not produce documents. Missing/failed web sources
are not selected for replacement, so their existing rows remain.

The entire batch is parsed, split using `SentenceSplitter(256, overlap=50)`,
cleaned and embedded before the write transaction begins. It uses the existing
metadata-aware embedding content, model and 768 dimensions. No chunk deduplication
occurs: distinct positions and intentional overlaps are retained, even when text
is equal. Empty sources, incomplete embedding results and invalid vectors fail
before deletion.

One transaction locks the existing LlamaIndex literature table against concurrent
writers, deletes the selected source rows and inserts all replacements. Readers
continue to see the committed index until the replacement commits. Insert errors
roll back the deletion and any partial inserts. The writer lock also prevents two
concurrent first-time ingestions from both appending the same source. Its lock
wait timeout is 30 seconds. No schema or unique-index changes are needed.

For a single authoritative PDF, use the Python function's optional `pdf_paths`:

```python
from pathlib import Path
from app.frameworks.llamaindex.literature_ingestion import ingest_literature_with_llamaindex

ingest_literature_with_llamaindex(pdf_paths=[Path("path/to/document.pdf")])
```

The existing `/rag/ingest_docs` endpoint and its authentication are unchanged.
LangChain experiments, snapshots and retrieval behavior are unchanged.

## Reproducible historical cleanup

For this migrated corpus, replacing all seven current PDFs is safer than guessing
which historical equal-text records can be removed. Historical file paths, parser
versions and metadata budgets can produce different chunk boundaries, so the
fresh count need not equal the historical count minus the duplicate estimate.
The two existing web-source rows are preserved, not re-fetched or silently erased.

Run from the repository root with the existing local `.env`:

```powershell
.venv/Scripts/python.exe scripts/rebuild_literature.py --apply
```

This local-database-only maintenance command saves a pre-change CSV table backup
and JSON report in ignored `scratch/literature-rebuild-<timestamp>/`, captures the
requested top-five query, prepares the PDFs, tests a real rollback after a partial
insert, then replaces the PDF corpus. It repeats one PDF and the complete PDF
corpus, checking stable counts, source preservation, exact prepared chunk
occurrences and intentional overlaps. It finally captures the same query's new
top five using the unchanged `RAG_K=5`. It does not start the API or scheduler.

The CSV contains every original table column, including IDs and vectors. Retain
it until satisfied with the rebuild. Restoration, if needed, should be scoped to
this table and performed transactionally after stopping competing ingestion;
the maintenance command does not automatically overwrite a successful rebuild.
