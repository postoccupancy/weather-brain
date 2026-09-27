"""Shared PostgreSQL configuration; no connections are opened at import time."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def sqlalchemy_database_url(database_url: str = DATABASE_URL) -> str:
    if not database_url:
        raise RuntimeError("Missing required env var: DATABASE_URL")
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgres"}:
        raise ValueError("DATABASE_URL must be a plain PostgreSQL URL")
    # psycopg 3 supports both SQLAlchemy's sync and async engines.
    url = url.set(drivername="postgresql+psycopg")
    options = url.query.get("options", "")
    url = url.update_query_dict({"options": f"{options} -csearch_path=public,extensions".strip()})
    return url.render_as_string(hide_password=False)
