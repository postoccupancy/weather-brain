from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, field_validator
import psycopg

from app.api.auth import require_ingest_token, require_status_token
from app.retrieval.structured import deployments

router = APIRouter(prefix="/nodes")
NodeId = Annotated[str, Path(min_length=1, max_length=255)]


class StartDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    location_label: str | None = Field(default=None, max_length=500)
    latitude: FiniteFloat | None = Field(default=None, ge=-90, le=90)
    longitude: FiniteFloat | None = Field(default=None, ge=-180, le=180)
    altitude_m: FiniteFloat | None = None
    notes: str | None = Field(default=None, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: AwareDatetime | None = None

    @field_validator('name')
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError('Name must not be blank')
        return value.strip()


class ChangeDeployment(StartDeployment):
    expected_deployment_id: UUID


class EndDeployment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_deployment_id: UUID
    ended_at: AwareDatetime | None = None


def execute(operation):
    try:
        return operation()
    except (deployments.DeploymentConflict, psycopg.errors.UniqueViolation) as exc:
        detail = str(exc) if isinstance(exc, deployments.DeploymentConflict) else 'A deployment is already active'
        raise HTTPException(status_code=409, detail=detail) from exc
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail='Deployment database unavailable') from exc


@router.get('/{node_id}/deployment', dependencies=[Depends(require_status_token)])
def get_current(node_id: NodeId):
    return {'ok': True, 'node_id': node_id, 'deployment': execute(lambda: deployments.current_deployment(node_id))}


def manage(node_id, action, payload):
    result = execute(lambda: deployments.manage_deployment(node_id, action, payload.model_dump()))
    return {'ok': True, 'node_id': node_id, **result}


@router.post('/{node_id}/deployment/start', dependencies=[Depends(require_ingest_token)])
def start(node_id: NodeId, payload: StartDeployment):
    return manage(node_id, 'start', payload)


@router.post('/{node_id}/deployment/change', dependencies=[Depends(require_ingest_token)])
def change(node_id: NodeId, payload: ChangeDeployment):
    return manage(node_id, 'change', payload)


@router.post('/{node_id}/deployment/end', dependencies=[Depends(require_ingest_token)])
def end(node_id: NodeId, payload: EndDeployment):
    return manage(node_id, 'end', payload)
