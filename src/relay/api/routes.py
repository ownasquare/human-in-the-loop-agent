"""Versioned Relay API routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from relay.models import (
    ApprovalDecisionRequest,
    ProposalStatus,
    RunCreateRequest,
    RunStatus,
)
from relay.runtime import RelayRuntime

router = APIRouter(prefix="/api/v1")


def get_runtime(request: Request) -> RelayRuntime:
    return cast(RelayRuntime, request.app.state.runtime)


RuntimeDependency = Annotated[RelayRuntime, Depends(get_runtime)]


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "relay", "version": "0.1.0"}


@router.get("/readiness")
async def readiness(runtime: RuntimeDependency) -> dict[str, Any]:
    return runtime.readiness()


@router.get("/capabilities")
async def capabilities(runtime: RuntimeDependency) -> dict[str, Any]:
    return runtime.capabilities()


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: RunCreateRequest,
    runtime: RuntimeDependency,
) -> dict[str, Any]:
    result = await runtime.start(
        payload.instruction,
        mode=payload.mode,
        idempotency_key=payload.idempotency_key,
    )
    return result.model_dump(mode="json")


@router.get("/runs")
async def list_runs(
    runtime: RuntimeDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    items, total = runtime.list_runs(limit=limit, offset=offset, status=run_status)
    return {"items": [item.model_dump(mode="json") for item in items], "total": total}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, runtime: RuntimeDependency) -> dict[str, Any]:
    return (await runtime.get_detail(run_id)).model_dump(mode="json")


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, runtime: RuntimeDependency) -> dict[str, Any]:
    return (await runtime.cancel(run_id)).model_dump(mode="json")


@router.get("/approvals")
async def list_approvals(
    runtime: RuntimeDependency,
    approval_status: Annotated[ProposalStatus | None, Query(alias="status")] = (
        ProposalStatus.PENDING
    ),
    run_id: str | None = None,
) -> dict[str, Any]:
    items = runtime.list_approvals(status=approval_status, run_id=run_id)
    return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}


@router.get("/approvals/{proposal_id}")
async def get_approval(proposal_id: str, runtime: RuntimeDependency) -> dict[str, Any]:
    return runtime.approval(proposal_id).model_dump(mode="json")


@router.post("/approvals/{proposal_id}/decisions", status_code=status.HTTP_202_ACCEPTED)
async def decide_approval(
    proposal_id: str,
    payload: ApprovalDecisionRequest,
    runtime: RuntimeDependency,
) -> dict[str, Any]:
    return (await runtime.decide(proposal_id, payload)).model_dump(mode="json")


@router.get("/audit-events")
async def list_audit_events(
    runtime: RuntimeDependency,
    run_id: str | None = None,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    items, total = runtime.repository.list_audit_events(
        run_id=run_id, after_sequence=after_sequence, limit=limit
    )
    return {"items": [item.model_dump(mode="json") for item in items], "total": total}


@router.get("/audit-events/export")
async def export_audit_events(
    runtime: RuntimeDependency,
    run_id: str | None = None,
) -> JSONResponse:
    items, total = runtime.repository.list_audit_events(run_id=run_id, limit=None)
    chains: dict[str, bool] = {}
    for event in items:
        if event.run_id not in chains:
            chains[event.run_id] = runtime.repository.verify_audit_chain(event.run_id)
    return JSONResponse(
        content={
            "exported_at": datetime.now(UTC).isoformat(),
            "items": [item.model_dump(mode="json") for item in items],
            "total": total,
            "chain_valid": all(chains.values()) if chains else True,
        },
        headers={"Content-Disposition": "attachment; filename=relay-audit.json"},
    )


@router.get("/connectors")
async def connectors(runtime: RuntimeDependency) -> dict[str, Any]:
    items = runtime.connectors()
    return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}


@router.post("/demo/reset")
async def reset_demo(runtime: RuntimeDependency) -> dict[str, Any]:
    return await runtime.reset_demo()
