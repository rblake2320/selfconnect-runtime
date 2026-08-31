"""Session manager + durable job queue (design §3.1).

A job is a unit of work (one kernel run) keyed by an idempotency key: the same
key never runs twice — a duplicate enqueue returns the existing job. Jobs are
durable (SQLite), so a crash mid-run leaves a `running` row that
`recover_all()` reclassifies through the kernel's crash-recovery
(resume / safe_reissue / quarantine) — never double-firing a side effect.
"""
from __future__ import annotations

import threading
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
    def __init__(self, store: Store, kernel_factory: KernelFactory,
                 package_guard: Optional[Callable[[], "object"]] = None):
        self.store = store
        self.kernel_factory = kernel_factory
        # G3: a zero-arg callable returning a LoadResult-like object with `.ok`
        # and `.error`; re-verifies the bound package at every run start.
        self.package_guard = package_guard
        # G5: per-job cancel state.
        self._cancel: dict[str, threading.Event] = {}
        self._runners: dict[str, object] = {}
        self._lock = threading.Lock()

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
        # G3: re-verify the bound package at execution start. A package that was
        # tampered on disk or revoked after install is refused here — the run
        # never touches the model or a tool.
        if self.package_guard is not None:
            result = self.package_guard()
            if not getattr(result, "ok", False):
                self.store.job_set_status(job_id, "refused")
                detail = getattr(result, "error", "package_unverified")
                return RunResult(job["session_id"], "", 0, f"package_unverified:{detail}")
        self.store.job_set_status(job_id, "running")
        kernel = self.kernel_factory(self.store, job["session_id"])

        # G5: wire cooperative cancel + in-flight process-tree kill.
        flag = threading.Event()
        with self._lock:
            self._cancel[job_id] = flag
            runner = getattr(kernel, "sandbox_runner", None)
            if runner is not None:
                self._runners[job_id] = runner
        kernel.cancel_check = flag.is_set
        try:
            result = kernel.run(job["session_id"], job["user_text"])
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)
                self._runners.pop(job_id, None)
        status = "cancelled" if result.stopped_reason == "cancelled" else self._status_for(result)
        self.store.job_set_status(job_id, status)
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
        """G5: signal cooperative stop AND kill any in-flight process tree, so
        no orphaned child survives a session cancel."""
        with self._lock:
            flag = self._cancel.get(job_id)
            runner = self._runners.get(job_id)
        if flag is not None:
            flag.set()
        if runner is not None:
            runner.kill_all()
        self.store.job_set_status(job_id, "cancelled")
        job = self.store.job_get(job_id)
        if job is not None:
            self.store.set_session_status(job["session_id"], "cancelled")

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
