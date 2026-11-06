Picking up from the previous note where I had set up an open source agent stack in Python and a cloud database for storing vector-embedded documents for the LLM to reference via RAG (retrieval augmented generation). 

> Historical research/configuration notes. Cloud hosting, Supabase and prototype
> details below do not describe the current Windows FastAPI/local PostgreSQL
> deployment. See the [current architecture](../README.md).

There are two separate models required for the RAG process:
1. a text embedding model -- not a full LLM, these are smaller models that take a piece of text and convert it to a vector array. The LLM can then read the vectors to determine if the text is 'semantically similar' to a given user question. For this agent I am using the 'nomic-text-embed' model from the Ollama library.
2. a large language model -- these are pretrained chatbot models that you can constrain to certain inputs and outputs. When building an agent, you aren't 'training' an AI, you are customizing it and orchestrating its actions, primarily via 'prompt engineering.' I have been testing a few different open source / open weight LLMs from the Ollama library, more on that below.

I got to a certain milestone by getting the system working, in terms of having a live FastAPI server and a PosgreSQL database filled with data from a microcontroller sensor. I wanted the agent to be able to read the data, and compare it to benchmark documents -- which meant that it needed to be able to both generate SQL queries and search a RAG vector store.

I experimented with two open source Python agent frameworks for this -- LangChain and LlamaIndex. Both provide low-code wrappers for invoking an LLM of your choice, and mechanisms to have it translate natural language to SQL queries. In this case I was using the Gemma2 LLM (the open weight variant of Gemini), running locally on my machine thanks to Ollama.

There are three main endpoints I'm trying to support:

`/rag/query` 
This calls the agent to give a response to your question, essentially just `response = answer_question(question)`. The underlying mechanics of the function vary depending on what SDK or agent framework you might be using -- in my code I have `llamaindex_answer_question()` and `langchain_answer_question()` which both use the same LLM. My reason for using LlamaIndex was to try its built-in [SQL Auto Vector Query Engine](https://developers.llamaindex.ai/python/examples/query_engine/sqlautovectorqueryengine/) feature, which automatically integrates natural language-to-SQL query results with semantic vector retrieval. This seemed to exactly match my use case, though in the end I think it was questionable whether this saved me much time, because it required some considerable wrangling to do what I wanted. Eventually it behaved as advertised, but I haven't gotten to do as much testing as I wanted. 
Regardless, the `/rag/query` chatbot can now answer questions like these:
- What was the average temperature yesterday? (natural language-to-SQL query producing a single number, phrased as a sentence e.g. "The average temperature yesterday was 71 degrees Fahrenheit"). 
- Were temperatures yesterday within safe limits for human comfort? (SQL query for min/max temperature first, then a follow-up search of the RAG vector store for documents related to 'safe temperatures for human comfort.' The response is something like "Yesterday the min was 66 degrees and the max was 74 degrees. According to XYZ document, temperatures yesterday were within safe limits for human comfort.")

`/rag/index` 
This second endpoint is for running a fixed SQL query to assemble hourly snapshots of the raw 2-second temperature readings, with min/max/average temperature, and a row count to assess any drop-outs. The idea is to embed these as reference RAG documents, and make them available for semantic similarity queries so the LLM can spot trends. This is an alternative way to have the LLM chat about the data, purely using RAG without doing direct SQL. 

I have some initial notes about how it works, although I haven't yet tested the quality of the results. The text embedding model (nomic-text-embed) takes in a JSON response returned from the SQL query, and saves the result to a "vector store." In this case I wanted my vector store in the same Supabase / PostgreSQL location as my other data, although there are several other options (e.g. ChromaDB, Vertex AI). To store the embeddings in a PostgreSQL table specifically requires the Python package `pgvector` that LangChain wraps into a PostgreSQL indexing function. ( `from langchain_postgres import PGVector \ vectorstore.add_documents(documents, ids)`. The vector store tables appear automatically in Supabase. 

Separate from the RAG vector store, I am archiving the snapshots as non-embedded data rows in a `snapshots` table, just to make them available for data visualization and direct SQL queries. As a result, the `/rag/index` endpoint has two functions for `index_documents_in_vectorstore(documents)` and `archive_documents_in_database(db_url, documents)`. 


`/rag/ingest_docs` 
This third endpoint is for building a literature reference vector store based on the PDF documents saved in `/retrieval/corpus/pdfs` and web pages in `/retrieval/corpus/urls`.  For this I used LlamaIndex to compare against LangChain, which I used for `/rag/index`. Here, `pgvector` looks like this: 
```
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.vector_stores.postgres import PGVectorStore 

storage_context = StorageContext.from_defaults(
    vector_store=PGVectorStore.from_params(params)
)

index = VectorStoreIndex(
    nodes, 
    storage_context=storage_context,
    embed_model=embed_model
    )

```

The other thing worth noting in this endpoint is the use of LlamaIndex's document embedding features, including `SimpleDirectoryReader`, `SimpleWebPageReader`, and `SentenceSplitter`. These are all nice compact functions like:

```
pdf_docs = SimpleDirectoryReader(
    input_dir=str(PDF_DIR),
    recursive=True,
).load_data()

web_docs = SimpleWebPageReader().load_data(urls)

splitter = SentenceSplitter(
        chunk_size=256, # tokens -- make sure to avoid exceeding the context window
        chunk_overlap=50, 
    )

nodes = splitter.get_nodes_from_documents(pdf_docs + web_docs)
```

In the `ingest_docs.py` code there are also `normalize_metadata()` and `clean_text()`steps needed to get from from raw PDFs and web pages to coherent metadata and text that doesn't error out on weird characters. ChatGPT generated these functions for me, I wish they weren't necessary, but there may always be a degree of manual data cleaning to make text embedding effective.

## Next steps

My next priority is to set up the Next / React / TS front-end app in the `/ui` directory. This will include the following core features:
* Live status dashboard with time series charts, time range filters, and aggregation metrics
* LLM-generated summary of currently selected filter state that changes in response to filter adjustments
* Chatbot that can answer deeper-dive questions about the current filter state, and change the filter state to show data views

The agent and the UI state are closely tied. I am planning to incorporate the Zustand state management library for React, and also considering moving away from LangChain/LlamaIndex to the Vercel / Next AI SDK, to reduce mental friction. 
