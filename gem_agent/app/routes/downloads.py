"""Per-file downloads and the zip of a run's relevant bids. Files come from the shared document cache, but
only for bids in one of the user's own runs."""
from __future__ import annotations

import io
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from gem_agent.app.auth import current_user
from gem_agent.cache.names import display_files, download_name
from gem_agent.graph.views import bid_state, with_names

router = APIRouter()


def _outcomes(request: Request, user: str, run_id: str) -> dict:
    manager = request.app.state.manager
    try:
        manager.view(user, run_id)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail="no such run") from exc
    return with_names(manager.s.store.outcomes(run_id), request.app.state.cache)


@router.get("/api/runs/{run_id}/files/{b_id}/{sha}")
def file(run_id: str, b_id: int, sha: str, request: Request, user: str = Depends(current_user)) -> FileResponse:
    """The file, saved under what it is, not GeM's upload number."""
    outcome = _outcomes(request, user, run_id).get(b_id)
    groups = display_files(outcome.files) if outcome else []
    group = next((g for g in groups if g.record.sha256 == sha and g.record.path), None)
    if group is None:  # a copy shown under another: same content, so any record with this hash will do
        group = next((g for g in groups for r in g.records if r.sha256 == sha and r.path), None)
    path = request.app.state.cache.root / group.record.path if group and group.record.path else None
    if group is None or outcome is None or path is None or not path.exists():
        raise HTTPException(status_code=404, detail="no such file")
    return FileResponse(path, filename=download_name(outcome.bid.bid_number, group))


@router.get("/api/runs/{run_id}/zip")
def zip_all(run_id: str, request: Request, user: str = Depends(current_user)) -> StreamingResponse:
    outcomes = _outcomes(request, user, run_id)
    root = request.app.state.cache.root
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for o in outcomes.values():
            if bid_state(o) != "relevant":
                continue
            folder = o.bid.bid_number.replace("/", "-")
            for group in display_files(o.files):  # each document once, under what it is
                f = group.record
                if f.status == "ok" and f.path and (root / f.path).exists():
                    z.write(root / f.path, f"{folder}/{download_name(o.bid.bid_number, group)}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})
