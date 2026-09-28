# Explicit MVP answering

The existing GET/POST `/rag/query` endpoints use the explicit runtime when
`framework=llamaindex` (the existing default). Authentication and the response
envelope (`framework`, `question`, `response`, `answer`, `metadata`) remain.
Restart FastAPI after updating; the shared factories are cached.

| Route | Work | Qwen calls | Embedding calls |
| --- | --- | --- | --- |
| `sql` | Generate SELECT, validate, execute, return rows | 1 | 0 |
| `literature` | Embed question, direct top-k literature search, synthesize | 1 | 1 |
| `sql+literature` | Generate/execute SELECT, retrieve literature, synthesize both | 2 | 1 |

Routing is deterministic and SQL-biased in `planning/knowledge_paths.py`.
Literature requires explicit knowledge/guidance intent, including definitions,
research, standards or recommendations. Observed-data questions with that intent
use both. Dates, month ranges, trends, statistics and unknown questions default
to SQL. Explaining observed trends is SQL; explaining a concept is literature.
Publication years alone do not turn research questions into observed-data queries.
Examples:

- "What was the average temperature yesterday?" -> SQL.
- "What humidity is recommended?" -> literature.
- "Was my room too hot yesterday?" -> SQL plus literature.

These are deliberately small English heuristics, not a general intent parser.
They can misclassify ambiguous wording; the selected route is returned to make
that visible. They never consult a model or automatically query snapshots.

## SQL and literature

The minimal SQL prompt uses the configured raw-reading table's reflected schema.
The old experimental prompt's unrelated oranges instruction is not in the MVP
prompt. A conservative tokenizer requires a single SELECT; SELECT INTO, locking
clauses, writes, multiple statements, comments, CTEs, dollar quoting and backslash
string escapes are rejected. A single SQL Markdown fence is accepted. Unsupported
generated SQL returns HTTP 422 without executing it or retrying model generation.

Execution uses a new PostgreSQL read-only transaction with a 30-second statement
timeout. PostgreSQL remains the authority for statement validity and transaction
read-only enforcement. This is not a general sandbox for arbitrary functions
available to the database role. The existing connection configuration is retained.
SQL-only `answer` and `response` are JSON text representing rows; structured rows
are also in `metadata.sql_result`. No natural-language answer call is added.

Literature retrieval directly calls the existing PGVectorStore with the question
embedding and `RAG_K` as top-k. There is no query transformation, auto-retrieval
planning, intermediate summarization or placeholder embedding. The table remains
`data_<RAG_LITERATURE_TABLE>` (normally `data_rag_literature_chunks`), with 768
dimensions and setup disabled. Source metadata, node IDs and similarity scores
are returned for the retrieved passages. The synthesis prompt numbers passages,
requests citations and requires acknowledging insufficient evidence, including
an empty result set. It does not invent fallback standards.

## Observability metadata

`metadata` contains:

- `request_id`, `route`, `selected_knowledge_paths`.
- `generated_sql` and `sql_result` (null when SQL was not selected).
- `literature_sources`: citation number, node ID, score and original source metadata.
- `timings_ms`: SQL generation, SQL execution/fetch, vector retrieval, embedding,
  LLM and total request duration.
- `llm_call_count`, `embedding_call_count`, and individual `llm_calls` with purpose,
  duration, outcome and available Ollama token/inference metrics.

Timings are inclusive and overlap. Vector retrieval includes embedding and store
access. Response `total_request` measures middleware entry through metadata
construction, excluding final serialization/network send; the existing logged
`request_total` measures through response send. No prompt, SQL or passage content
is added to timing logs; generated SQL and sources are returned only in the
authenticated response.

## Preserved experiments and scope

`framework=langchain` explicitly selects the existing snapshot-answering
experiment. LlamaIndex/LangChain snapshot and literature indexing, archive storage,
ingestion, scheduling and all existing endpoints are unchanged. The old
LlamaIndex query engines now live in `frameworks/llamaindex/experimental.py` and
are not selected by normal requests. The discarded placeholder index was removed
there too. Shared Ollama models and the 8192 LlamaIndex context remain unchanged.

The normal path is isolated in `answering.py`, with shared factories in
`runtime.py`, deterministic routing in `knowledge_paths.py`, and SELECT handling
in `generated_sql.py`. No dependencies, schema/data migrations, deployment or
frontend changes are required. Tests use mocked external I/O; live latency and
retrieval quality still need measurement.
