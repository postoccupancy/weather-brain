# LlamaIndex query timing

The normal runtime now uses [explicit MVP answering](mvp-answering.md): one Qwen
call for SQL or literature, two for combined queries, and no cold placeholder
embedding. The logging mechanism described here remains; auto-retrieval,
transformation, and six-call execution details below document the retained
`experimental.py` orchestration rather than the current endpoint path.

Restart FastAPI and use GET or POST `/rag/query` with `framework=llamaindex`
(or the configured LlamaIndex default). No new environment variables or packages
are required. INFO records from `uvicorn.error.rag_timing` appear with the normal
Uvicorn logging configuration. Each line has a generated `request_id`, `stage`,
`duration_ms`, and `outcome`. Filter by request ID to follow one request.

The final `request_total` line includes HTTP status, `llm_calls`, and
`embedding_calls`. It measures from middleware entry through the final response
send, including the sync worker wait, engine initialization and serialization.
Exceptions are re-raised unchanged and still produce a timing summary; status
can be `None` when an outer error handler has not sent the response yet.

Stages:

- `engine_initialization`: cached factory lookup, or cold engine construction.
- `llm_call`: one synchronous Ollama chat invocation, with purpose and call number.
  When returned by Ollama, the same line includes `prompt_tokens`
  (`prompt_eval_count`), `output_tokens` (`eval_count`), `prompt_eval_ms`
  (`prompt_eval_duration`) and `generation_ms` (`eval_duration`). Server durations
  are converted from nanoseconds to milliseconds. Missing metrics are omitted,
  not estimated or treated as zero. Responses and model options are unchanged.
- `embedding_call`: one synchronous Ollama embedding invocation, including batch
  requests as one call. Purpose identifies initialization versus retrieval.
- `sql_generation`: text-to-SQL prediction, including prompt formatting/output
  parsing within `predict`, but excluding SQL execution.
- `postgres_cursor_execute`: SQLAlchemy DBAPI execution, including schema
  introspection and vector queries. Excludes result fetching and pool wait.
- `postgres_query_and_fetch`: generated SQL execution including connection
  acquisition, result fetching and formatting.
- `vector_literature_retrieval`: auto-retriever planning, embedding and vector lookup.
- `vector_store_query_and_fetch`: vector store access, including lazy connection
  initialization and result fetching, excluding planning and embedding.
- `literature_answer_synthesis`: literature summarization; also the final answer
  when the selector chooses the literature-only branch.
- `final_answer_synthesis`: the combined SQL/literature answer prediction.
- `query_execution`, `route_selection`, `query_transformation`, and
  `retrieval_planning`: additional inclusive phase timings.

Durations overlap: do not sum parent phases and their child calls. These are
application wall times, not Ollama GPU-only generation or PostgreSQL server-only
execution times. Counters count attempted model calls, including failed calls.
These diagnostics cover the current synchronous, non-streaming query path.
Other endpoints and LangChain requests emit no diagnostic records. New logs
contain no questions, prompts, SQL text, credentials, document content or answers;
existing library logging is unchanged.

## Execution path findings

The installed libraries and an offline test of the actual engine confirm six
sequential Qwen calls for SQL followed by nonempty literature retrieval:

1. `SQLJoinQueryEngine._query`: its default `PydanticSingleSelector` calls Qwen
   with the `SingleSelection` tool to choose SQL versus literature.
2. `NLSQLRetriever.retrieve_with_metadata`: generates SQL. The configured
   `synthesize_response=False` avoids a separate SQL-answer LLM call.
3. `SQLAugmentQueryTransform._run`: transforms the original question using SQL results.
4. `VectorIndexAutoRetriever.generate_retrieval_spec`: generates the vector query,
   metadata filters and requested result count.
5. `SimpleSummarize.get_response`: summarizes the retrieved literature.
6. `SQLJoinQueryEngine._query_sql_other`: synthesizes the combined final answer.

A literature-only route normally uses three calls (selection, retrieval planning,
literature synthesis). If the transform returns `None` or `LOOKUP: None`, the SQL
route stops after three calls and returns the existing SQL response without
literature work or combined synthesis. The stop parser ignores surrounding
whitespace, case, and whitespace around the prefix separator. It matches the
whole sentinel, so real queries containing the word "None" are not skipped.
Real lookup queries are passed through unchanged. Empty retrieval results can skip literature
synthesis. Actual request counters are authoritative; six is not unconditional.

Potential extra work, deliberately left unchanged:

- `get_llamaindex_query_engine` embeds a placeholder document into an in-memory
  index, then discards that index. The first successful factory call therefore
  adds an embedding invocation; warmed calls normally have just the query
  embedding. A failed initialization may repeat this on the next request.
- Literature is summarized before a second LLM call synthesizes the combined
  answer. Query transformation and retrieval planning are also separate calls.
- Cold construction performs SQLAlchemy schema reflection, initializes clients,
  and constructs the vector store. Timings separate these from warm query work.
- `TemperatureSafetyEngine` is defined but is not used by this endpoint.

The user's live timing run confirmed six sequential calls and 36.997 seconds
before the stop-parser fix. Offline regression tests now confirm three calls
for no-lookup results and six for real lookups. No post-fix live duration has
been measured during implementation. Comparing the endpoint with one direct
Qwen generation is not an equivalent workload.

## Removal points

Instrumentation lives in `frameworks/llamaindex/diagnostics.py`. To remove it,
restore runtime's original class imports, remove timing scopes from
`answering.py`, and remove the middleware import/registration in `main.py`.
The stop-parser fix is separately wired through `check_stop_parser` in runtime;
removing timing does not require reverting it. No third-party code, prompts,
models, SQL or response envelope was changed. The LlamaIndex context remains
8192. SQLAlchemy listeners are registered once at module import and
only record work within an enabled LlamaIndex request context.
