"""FastAPI service surface (design §3.1, §3.7).

Localhost-only by default. A non-loopback bind is refused unless TLS + auth
are configured. Token auth (Bearer) resolves a subject+role; every route is
guarded by the RBAC matrix. REST covers session/job lifecycle, approval, and
evidence export; a WebSocket streams run events.

The kernel loop is synchronous; run routes execute it inline (fast for the
mock/scripted adapter and fine for a single-tenant self-hosted service). WS
streaming replays journaled events for a job.
"""
from __future__ import annotations

import ipaddress
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from pydantic import BaseModel

from .rbac import AccessDenied, require
from .sessions import KernelFactory, SessionManager
from .state import Store


class BindRefused(Exception):
    pass


def check_bind(host: str, tls: bool, auth: bool) -> None:
    """Refuse a non-loopback bind without both TLS and auth."""
    try:
        ip = ipaddress.ip_address(host)
        is_loopback = ip.is_loopback
    except ValueError:
        is_loopback = host in ("localhost",)
    if not is_loopback and not (tls and auth):
        raise BindRefused(
            f"refusing non-loopback bind to {host!r} without TLS and auth")


class RunRequest(BaseModel):
    user_text: str
    idem_key: str


class ApprovalRequest(BaseModel):
    approval_id: str
    approver: str


def create_app(store: Store, kernel_factory: KernelFactory,
               evidence_key: Optional[bytes] = None) -> FastAPI:
    app = FastAPI(title="SelfConnect Runtime", version="0.2.0")
    manager = SessionManager(store, kernel_factory)

    def auth(authorization: str = Header(default="")) -> dict:
        token = authorization[7:] if authorization.startswith("Bearer ") else authorization
        subject = store.token_get(token)
        if subject is None:
            raise HTTPException(status_code=401, detail="invalid or missing token")
        return subject

    def guard(action: str):
        def _dep(subject: dict = Depends(auth)) -> dict:
            try:
                require(subject["role"], action)
            except AccessDenied as e:
                raise HTTPException(status_code=403, detail=str(e))
            return subject
        return _dep

    @app.post("/runs")
    def create_run(req: RunRequest, subject: dict = Depends(guard("run"))):
        job = manager.enqueue(req.user_text, req.idem_key)
        result = manager.run_job(job.job_id)
        return {"job_id": job.job_id, "session_id": job.session_id,
                "deduped": job.deduped, "stopped_reason": result.stopped_reason,
                "final_text": result.final_text,
                "pending_approval": result.pending_approval}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, subject: dict = Depends(guard("read_status"))):
        job = manager.status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job

    @app.get("/jobs")
    def list_jobs(subject: dict = Depends(guard("read_status"))):
        return {"jobs": manager.list_jobs()}

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, subject: dict = Depends(guard("cancel"))):
        if manager.status(job_id) is None:
            raise HTTPException(status_code=404, detail="no such job")
        manager.cancel(job_id)
        return {"job_id": job_id, "status": "cancelled"}

    @app.post("/jobs/{job_id}/approve")
    def approve(job_id: str, req: ApprovalRequest,
                subject: dict = Depends(guard("approve"))):
        job = manager.status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        kernel = kernel_factory(store, job["session_id"])
        kernel.approve(job["session_id"], req.approval_id, req.approver)
        result = manager.resume_job(job_id)
        return {"job_id": job_id, "stopped_reason": result.stopped_reason,
                "final_text": result.final_text,
                "pending_approval": result.pending_approval}

    @app.post("/jobs/{job_id}/deny")
    def deny(job_id: str, req: ApprovalRequest,
             subject: dict = Depends(guard("approve"))):
        job = manager.status(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        kernel = kernel_factory(store, job["session_id"])
        kernel.deny(job["session_id"], req.approval_id, req.approver)
        result = manager.resume_job(job_id)
        return {"job_id": job_id, "stopped_reason": result.stopped_reason,
                "final_text": result.final_text}

    @app.get("/sessions/{session_id}/ledger")
    def read_ledger(session_id: str, subject: dict = Depends(guard("read_ledger"))):
        from .ledger import Ledger
        v = Ledger(store).verify(session_id)
        return {"session_id": session_id, "ok": v.ok, "count": v.count,
                "head": v.head, "error": v.error}

    @app.websocket("/ws/jobs/{job_id}")
    async def ws_events(websocket: WebSocket, job_id: str):
        await websocket.accept()
        token = websocket.headers.get("authorization", "")
        token = token[7:] if token.startswith("Bearer ") else token
        subject = store.token_get(token)
        if subject is None:
            await websocket.close(code=4401)
            return
        try:
            require(subject["role"], "read_status")
        except AccessDenied:
            await websocket.close(code=4403)
            return
        job = manager.status(job_id)
        if job is None:
            await websocket.close(code=4404)
            return
        for entry in store.journal_all(job["session_id"]):
            await websocket.send_json({"seq": entry["seq"], "state": entry["state"]})
        await websocket.close()

    app.state.manager = manager
    return app
