"""Run history, results, restore from quarantine."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from gem_agent.app.auth import current_user
from gem_agent.graph.manager import NotBorderline, RulingFailed, RunNotRunning

router = APIRouter()


@router.get("/api/runs")
def list_runs(request: Request, user: str = Depends(current_user)) -> list[dict[str, Any]]:
    return request.app.state.manager.list_runs(user)


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
    try:
        return request.app.state.manager.view(user, run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc


@router.get("/api/runs/{run_id}/status")
def get_status(run_id: str, request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
    try:
        return request.app.state.manager.status(user, run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc


@router.post("/api/runs/{run_id}/stop", status_code=204)
async def stop(run_id: str, request: Request, user: str = Depends(current_user)) -> Response:
    try:
        await request.app.state.manager.stop(user, run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc
    except RunNotRunning as exc:
        raise HTTPException(status_code=409, detail="this run is not running") from exc
    return Response(status_code=204)


@router.post("/api/runs/{run_id}/restore/{b_id}")
async def restore(run_id: str, b_id: int, request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
    try:
        return {"changed": await request.app.state.manager.restore(user, run_id, b_id)}
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run or bid") from exc
    except NotBorderline as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RulingFailed as exc:
        raise HTTPException(status_code=502, detail="could not record the ruling") from exc


class RuleBody(BaseModel):
    decision: Literal["counts", "not"]


@router.post("/api/runs/{run_id}/rule/{b_id}")
async def rule(run_id: str, b_id: int, body: RuleBody, request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
    try:
        return {"changed": await request.app.state.manager.rule(user, run_id, b_id, body.decision)}
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run or bid") from exc
    except NotBorderline as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RulingFailed as exc:
        raise HTTPException(status_code=502, detail="could not record the ruling") from exc


@router.delete("/api/runs/{run_id}/rule/{b_id}")
async def unrule(run_id: str, b_id: int, request: Request, user: str = Depends(current_user)) -> dict[str, Any]:
    try:
        return {"changed": await request.app.state.manager.unrule(user, run_id, b_id)}
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run or bid") from exc
