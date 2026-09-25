# SQL + LLM analysis of environmental sensor data

This API: 
1. collects live data from remote, microcontroller-based environmental sensors and stores to a cloud-hosted PostgreSQL database 
2. provides quantitative time series aggregation and analysis based on SQL queries
3. creates a source material database for LLM chat queries from periodic data snapshots and uploaded PDFs and web links
4. combines numeric data calculations and semantic retrieval in summarizing current conditions and responding to chat queries

### Hardware
The MVP prototype uses an Espressif ESP32-S3-DevKitC-1 microcontroller with an AM2320 Digital Temperature & Humidity Sensor (I2C interface). 

### Database 
The PostgreSQL database is hosted on a Supabase free tier plan with the `pgvector` extension that enables vector storage and similarity search. 

### Backend Framework
The backend API uses the Python FastAPI framework, hosted in a Google Cloud Run serverless container on a free tier plan.

### React UI
The frontend (separate repo - `esp32_ui`) uses the Next.js server-side TypeScript-React framework, hosted on a Vercel free tier plan at https://esp32ui.vercel.app with Material UI design system and components from Devias Material Kit Pro. 

### LLM Server
Vector embedding, RAG retrieval, and LLM chat run on a local machine with Ollama. I am testing a variety of free open-weight LLMs running on Ollama, including gemma2 (Google), llama3 (Meta), gpt-oss (OpenAI), and qwen2.5 (Alibaba). For text embedding, I am testing the bge-m3 and nomic-embed-text models. 

### Agent Development Frameworks
The LLM chat uses a multi-agent framework with Planning, Retrieval, and Execution personas.

1. **Planning**
   - interpret the user question
   - infer intent, metric, and time range
   - choose deterministic SQL retrieval, vector retrieval, or a hybrid path
2. **Retrieval**
   - fetch raw readings, aggregated readings, snapshots, or literature/doc chunks
   - combine structured and unstructured context for grounded answers
3. **UI Execution**
   - surface answers, charts, citations, and next actions in the separate `esp32_ui` frontend
   - keep the backend focused on APIs, grounding, and orchestration rather than presentation

I am currently testing three open source agent development frameworks:

#### **LangChain** - Python
* langchain-ollama integration 
* langchain-text-splitters - packages for splitting up PDFs and websites as embeddable chunks of text with metadata

#### **LlamaIndex** - Python
* llama-index-llms-ollama integration
* SentenceSplitter, SimpleWebPageReader, SimpleDirectoryReader split up PDFs and websites
* SQLAutoVectorQueryEngine integrates SQL queries with RAG retrieval

#### **Vercel AI SDK** - TypeScript
* (details to come)


## Project Structure

`weather-brain/`  
`├── server/                       # Server root`  
`│   ├── app/                      # FastAPI application package`  
`│   │   ├── api/                  # HTTP routes (ingest, timeseries, weather, rag)`  
`│   │   ├── planning/             # Query planning and agent workflows`  
`│   │   ├── retrieval/            # Structured, vector, and state retrieval flows`  
`│   │   ├── execution/            # Answer synthesis and response shaping`  
`│   │   ├── frameworks/           # LangChain and LlamaIndex runtime adapters`  
`│   │   ├── providers/            # Provider-specific config (currently Ollama)`  
`│   │   ├── external/             # External API provider adapters`  
`│   │   ├── retrieval/corpus/     # Reference corpus assets used for ingestion`  
`│   │   └── main.py               # FastAPI entry point`  
`├── device/                       # MicroPython scripts for ESP32-S3-DevKitC-1`  
`├── docs/                         # Project documentation and architecture notes`  
`├── .env.example                  # Example environment vars`  
`├── requirements.txt              # Python dependencies`  
`└── README.md                     # This file`


---

## API Endpoints

### Ingestion

`POST /ingest`  
Ingests sensor payloads from devices.

### Health and Status

`GET /ping`  
Basic health check for API and backend service wiring.

`GET /latest`  
Returns the latest ingested reading when authorized.

### Time-Series Queries

`GET /timeseries`  
Returns filtered time-series data based on query parameters such as `table`, `start_ts`, `end_ts`, `device_id`, `bucket`, and `aggregate_mode`.

`GET /timeseries/summary`  
Returns summary statistics for the raw readings table over an optional time range and device filter.

### Weather Data Enrichment

`GET /weather/hourly`  
Fetches hourly weather data from NOAA or Open-Meteo for comparison with sensor readings.

### RAG & Semantic Endpoints

* `GET /rag/query` and `POST /rag/query` — answer natural-language questions using planning plus hybrid retrieval over structured sensor data and indexed documents
* `POST /rag/index` — batch-embed recent time-series snapshots into the vector store
* `POST /rag/rebuild` — rebuild snapshot indexing from the full history
* `POST /rag/ingest_docs` — split PDFs and web pages into chunks and embed them in the document vector store


## Installation

### Prerequisites

* **Python 3.11+**

* **PostgreSQL** instance with credentials available (e.g. Supabase)

* `pgvector` extension installed in PostgreSQL for vector embeddings (in Supabase UI, install `vector` under Database > Extensions)

### Local Setup

Clone the repository and install dependencies:

`git clone https://github.com/postoccupancy/weather-brain.git`  
`cd weather-brain`  
`python -m venv .venv`  
`. ./.venv/bin/activate`  
`pip install -r requirements.txt`

Set up environment variables (see **Configuration** below), then start the API:

`cd server`  
`uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`

If you are opening the repo inside the `orcasound-next` devcontainer, keep using that container's interpreter and environment. The root `.venv` is the default for this workspace, and `.venv-orca` is the extra local copy for the other workspace.



## Configuration

Environment variables are used to control database connections. Copy `.env.example` to `.env` and fill in the required values.



## Documentation Structure

This repository follows a structured documentation layout inspired by best practices. The primary documentation is housed under the `docs/` folder. Key sections include:

**Technical Notes**

* [`docs/2025-12-17-open-source-agent-stack.md`](/docs/2025-12-17-open-source-agent-stack.md) — Mapping out the open source agent development toolkit. 
* [`docs/2026-01-27-rag-setup-and-next-steps.md`](/workspaces/weather-brain/docs/2026-01-27-rag-setup-and-next-steps.md) — Discussion of how the RAG endpoints work, and how I plan to use them in the visualization interface.
* [`docs/architecture.md`](/workspaces/weather-brain/docs/architecture.md) — Current architecture with explicit Planning, Retrieval, and UI Execution layers.



## Contributing

Contributions are welcome\! For structured guidelines, see the `CONTRIBUTING.md` once created. For now:

1. Fork the repo

2. Create a descriptive branch

3. Open a pull request with context and tests (when available)

---

## License

This project is open source and released under the BSD-3 Clause License.

---

## What’s Next / Roadmap

Details to come...

---

## Testing

The current automated tests focus on the new Planning -> Retrieval -> Execution architecture boundaries rather than only route wiring.

Covered areas:

* auth dependency checks for `STATUS_TOKEN`, `INGEST_TOKEN`, and `RAG_TOKEN`
* planning logic in `planning/planner.py`
* structured retrieval in `retrieval/structured/timeseries.py`
* structured retrieval dispatch in `retrieval/structured/weather.py`
* vector-ingestion helpers in `retrieval/vector/ingest_docs.py`

Run the test suite from the root `.venv`:

```bash
cd weather-brain
. ./.venv/bin/activate
python -m pytest /workspaces/weather-brain/tests
```

```PowerShell
.\.venv\Scripts\Activate.ps1
```

Current result on this branch: `21 passed`
