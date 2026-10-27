from __future__ import annotations

import os
import time
import logging

from app.retrieval.vector.service import index_snapshots


from app.database import DATABASE_URL
SNAPSHOT_DATA_TABLE = os.getenv("SNAPSHOT_DATA_TABLE", "")


def index_loop(interval_seconds: int = 3600) -> None:
    print("Starting indexing loop...")
    while True:
        try:
            from app.retrieval.vector.correction_rebuilds import process_correction_rebuilds
            process_correction_rebuilds()
        except Exception:
            logging.getLogger(__name__).exception('Correction rebuild processing unavailable; will retry')
        try:
            source = os.getenv("SNAPSHOT_SOURCE", "signal_buckets")
            result = index_snapshots(
                db_url=DATABASE_URL,
                framework="langchain" if source == "signal_buckets" else None,
                archive=SNAPSHOT_DATA_TABLE,
                source=source,
                lookback_hours=3,
            )
            print(result)
        except Exception:
            logging.getLogger(__name__).exception("Snapshot indexing failed; will retry next interval")
        time.sleep(interval_seconds)
