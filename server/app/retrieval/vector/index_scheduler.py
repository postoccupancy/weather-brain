from __future__ import annotations

import os
import time

from app.retrieval.vector.service import index_snapshots


from app.database import DATABASE_URL
SNAPSHOT_DATA_TABLE = os.getenv("SNAPSHOT_DATA_TABLE", "")


def index_loop(interval_seconds: int = 3600) -> None:
    print("Starting indexing loop...")
    while True:
        result = index_snapshots(
            db_url=DATABASE_URL,
            framework=None,
            archive=SNAPSHOT_DATA_TABLE,
            lookback_hours=1,
        )
        print(result)
        time.sleep(interval_seconds)
