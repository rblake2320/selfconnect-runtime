"""Session manager + durable job queue (design §3.1).

A job is a unit of work (one kernel run) keyed by an idempotency key: the same
key never runs twice — a duplicate enqueue returns the existing job. Jobs are
durable (SQLite), so a crash mid-run leaves a `running` row that
`recover_all()` reclassifies through the kernel's crash-recovery
(resume / safe_reissue / quarantine) — never double-firing a side effect.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from .kernel import Kernel, RunResult
from .state import Store

KernelFactory = Callable[[Store, str], Kernel]


@dataclass
class Job:
    job_id: str
    session_id: str
    status: str
    deduped: bool = False


class SessionManager:
    def __init__(self, store: Store, kernel_factory: KernelFactory):
        self.store = store
        self.kernel_factory = kernel_factory

    # -------------------------------------------------------- enqueue
    def enqueue(self, user_text: str, idem_key: str) -> Job:
        session_id = self.store.create_session()
        job_id = uuid.uuid4().hex
        rec = self.store.job_upsert(job_id, session_id, idem_key, "queued", user_text)
        # On dedupe the speculative session is left empty (harmless); the
        # original job is returned so no duplicate run is ever scheduled.
        return Job(rec["job_id"], rec["session_id"], rec["status"], deduped=rec["deduped"])

    # ------------------------------------------------------------- run
    def run_job(self, job_id: str) -> RunResult:
        job = self.store.job_get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] in ("done", "cancelled"):
            # idempotent: already terminal
            return RunResult(job["session_id"], "", 0, job["status"])
        self.store.job_set_status(job_id, "running")
        kernel = self.kernel_factory(self.store, job["session_id"])
        result = kernel.run(job["session_id"], job["user_text"])
        self.store.job_set_status(job_id, self._status_for(result))
        return result

    def resume_job(self, job_id: str) -> RunResult:
        job = self.store.job_get(job_id)
        kernel = self.kernel_factory(self.store, job["session_id"])
        result = kernel.resume(job["session_id"])
        self.store.job_set_status(job_id, self._status_for(result))
        return result

    def _status_for(self, result: RunResult) -> str:
        if result.stopped_reason == "completed":
            return "done"
        if result.stopped_reason == "awaiting_approval":
            return "awaiting_approval"
        return "needs_review"

    # ---------------------------------------------------------- cancel
    def cancel(self, job_id: str) -> None:
        self.store.job_set_status(job_id, "cancelled")
        self.store.set_session_status(self.store.job_get(job_id)["session_id"], "cancelled")

    # --------------------------------------------------------- recovery
    def recover_all(self) -> list[dict]:
        """After a crash, reclassify every job left `running`."""
        out = []
        for job in self.store.jobs_by_status("running"):
            kernel = self.kernel_factory(self.store, job["session_id"])
            report = kernel.recover(job["session_id"])
            new_status = "needs_review" if report.status == "quarantined" else (
                "done" if report.status == "clean" else "needs_review")
            self.store.job_set_status(job["job_id"], new_status)
            out.append({"job_id": job["job_id"], "recovery": report.status,
                        "status": new_status})
        return out

    def status(self, job_id: str) -> Optional[dict]:
        return self.store.job_get(job_id)

    def list_jobs(self) -> list[dict]:
        return self.store.jobs_all()
