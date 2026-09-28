from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from app.api.auth import require_ingest_token
from app.retrieval.structured.sql_queries import insert_signal_buckets


router = APIRouter()


class SignalBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket_start: datetime
    signal_id: str = Field(min_length=1, max_length=255)
    unit: str | None = Field(default=None, max_length=80)
    mean: FiniteFloat
    min: FiniteFloat
    max: FiniteFloat
    stddev: FiniteFloat = Field(ge=0)
    sample_count: int = Field(gt=0, le=100_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("bucket_start")
    @classmethod
    def bucket_start_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bucket_start must include a timezone")
        return value

    @model_validator(mode="after")
    def aggregate_bounds_are_consistent(self) -> "SignalBucket":
        if not self.min <= self.mean <= self.max:
            raise ValueError("min, mean, and max must satisfy min <= mean <= max")
        return self


class SignalBucketBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=255)
    buckets: list[SignalBucket] = Field(min_length=1, max_length=5_000)


@router.post("/ingest/signal-buckets", dependencies=[Depends(require_ingest_token)])
def ingest_signal_buckets(payload: SignalBucketBatch):
    result = insert_signal_buckets(
        node_id=payload.node_id,
        buckets=[bucket.model_dump() for bucket in payload.buckets],
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Database write failed")
    return {
        "ok": True,
        "accepted": result.accepted,
        "inserted": result.inserted,
        "duplicates": result.accepted - result.inserted,
        "deployment_id": result.deployment_id,
    }
