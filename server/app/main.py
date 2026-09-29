# app.py
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

# Load repo-root .env explicitly so behavior is stable across working directories.
DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=DOTENV_PATH)

from app.api.auth import require_ingest_token, require_status_token
from app.api.rag_router import router as rag_router
from app.api.signal_buckets_router import router as signal_buckets_router
from app.api.deployments_router import router as deployments_router
from app.api.timeseries_router import router as timeseries_router
from app.api.weather_router import router as weather_router
from app.retrieval.vector.index_scheduler import index_loop
from app.retrieval.structured.sql_queries import (
    insert_postgres,
)
from app.database import DATABASE_URL
from app.frameworks.llamaindex.diagnostics import RagTimingMiddleware

app = FastAPI()
app.add_middleware(RagTimingMiddleware)


class IngestPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_id: str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://esp32ui.vercel.app", "https://esp32-ui-252775611344.us-west1.run.app"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# add endpoints from rag_router.py to the main app, prefixed with /rag, and tagged as 'rag' in the FastAPI /docs UI
app.include_router(rag_router, prefix="/rag", tags=["rag"])
app.include_router(signal_buckets_router, tags=["ingest"])
app.include_router(deployments_router, tags=["deployments"])
app.include_router(timeseries_router, tags=["timeseries"])
app.include_router(weather_router, tags=["weather"])

### Index loop ###
@app.on_event("startup")
def start_index_loop():
    thread = threading.Thread(target=index_loop, daemon=True)
    thread.start()

latest_reading = None

@app.get("/ping")
def ping():
    return {"pong": True, "database_configured": bool(DATABASE_URL)}

@app.get("/latest", dependencies=[Depends(require_status_token)])
def get_latest():
    return latest_reading or {}

@app.post("/ingest", dependencies=[Depends(require_ingest_token)])
async def ingest(payload: IngestPayload, request: Request):
    global latest_reading

    data = payload.model_dump()
    raw_json = await request.json()
    if isinstance(raw_json, dict):
        for key, value in raw_json.items():
            data.setdefault(key, value)
    data["ts"] = datetime.now(timezone.utc).isoformat()

    safe_log = {"device_id": data.get("device_id"), "ts": data.get("ts")}
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), safe_log)

    if not DATABASE_URL:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "postgres": "error", "detail": "Database is not configured"},
        )

    result = insert_postgres(data)
    if not result or not result.data:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "postgres": "error", "detail": "Database write failed"},
        )

    latest_reading = data

    return {"ok": True, "postgres": "ok"}

if __name__ == "__main__":
    import uvicorn
    # LAN-exposed so ESP32 can reach it
    uvicorn.run(app, host="0.0.0.0", port=8000)
