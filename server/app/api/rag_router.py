import os
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import require_rag_token
from app.execution.answering.service import answer_question
from app.retrieval.vector.service import ingest_literature, index_snapshots


### FastAPI authentication layer ###
# 1. FastAPI maps custom header like X-RAG-Token to x_rag_token parameter
# 2. Attach dependencies=[Depends(...)] to the route to run a check before endpoint handler
# 3. server checks incoming requests for a specific header, 
# e.g. X-RAG-Token: a-long-secret-key
# 4. if it doesn't match, return 401 Unauthorized

SNAPSHOT_DATA_TABLE = os.getenv("SNAPSHOT_DATA_TABLE", "")

### To call these endpoints from terminal, use:
# curl -X POST http://127.0.0.1:8000/rag/[endpoint] -H "X-RAG-Token: [token]"


router = APIRouter()

class QueryReq(BaseModel):
    question: str

from app.database import DATABASE_URL

# this one is for browser use with a query param
@router.get("/query", dependencies=[Depends(require_rag_token)])
def query_get(
    question: str = Query(...),
    framework: str | None = Query(default=None),
):
    return answer_question(question, framework=framework)

# proper post request with a header
@router.post("/query", dependencies=[Depends(require_rag_token)])
def query(
    request: QueryReq,
    framework: str | None = Query(default=None),
):
    return answer_question(request.question, framework=framework)

@router.post("/rebuild", dependencies=[Depends(require_rag_token)])
def rebuild_snapshot_index(framework: str | None = Query(default=None)):
    return index_snapshots(
        db_url=DATABASE_URL,
        framework=framework,
        archive=SNAPSHOT_DATA_TABLE,
        lookback_hours=None,
    )

@router.post("/index", dependencies=[Depends(require_rag_token)])
def index_recent_snapshots(framework: str | None = Query(default=None)):
    return index_snapshots(
        db_url=DATABASE_URL,
        framework=framework,
        archive=SNAPSHOT_DATA_TABLE,
        lookback_hours=1,
    )

@router.post("/ingest_docs", dependencies=[Depends(require_rag_token)])
def ingest_literature_docs(framework: str | None = Query(default=None)):
    return ingest_literature(framework=framework)
