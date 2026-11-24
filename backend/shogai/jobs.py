"""In-memory background jobs with replayable progress events (served over SSE)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator

from .store import now_iso

MAX_JOBS = 100
HEARTBEAT_S = 15.0

log = logging.getLogger("shogai")


class Job:
    def __init__(self, kind: str, person_slug: str, person_name: str, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.person_slug = person_slug
        self.person_name = person_name
        self.label = label
        self.created = now_iso()
        self.status = "running"  # running | done | error
        self.events: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.task: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    async def emit(self, event: dict[str, Any]) -> None:
        async with self._cond:
            self.events.append({"ts": now_iso(), **event})
            self._cond.notify_all()

    async def finish(self, status: str, event: dict[str, Any]) -> None:
        async with self._cond:
            self.status = status
            if status == "done":
                self.result = event.get("result")
            else:
                self.error = event.get("message")
            self.events.append({"ts": now_iso(), **event})
            self._cond.notify_all()

    @property
    def finished(self) -> bool:
        return self.status != "running"

    async def stream(self, since: int = 0) -> AsyncIterator[tuple[int, dict[str, Any] | None]]:
        """Yield (index, event) from `since`, then live events; (-1, None) is a heartbeat."""
        idx = max(0, since)
        while True:
            async with self._cond:
                if idx >= len(self.events) and not self.finished:
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        pass
                new = self.events[idx:]
                done = self.finished
            if not new and not done:
                yield -1, None
                continue
            for event in new:
                yield idx, event
                idx += 1
            if done and idx >= len(self.events):
                return

    def snapshot(self, with_events: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "person": self.person_slug,
            "person_name": self.person_name,
            "label": self.label,
            "created": self.created,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "event_count": len(self.events),
        }
        if with_events:
            data["events"] = self.events
        return data


class JobManager:
    def __init__(self) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()

    def create(self, kind: str, person_slug: str, person_name: str, label: str) -> Job:
        job = Job(kind, person_slug, person_name, label)
        self._jobs[job.id] = job
        while len(self._jobs) > MAX_JOBS:
            oldest_id, oldest = next(iter(self._jobs.items()))
            if not oldest.finished:
                break
            self._jobs.pop(oldest_id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(reversed(self._jobs.values()))

    async def drain(self, timeout: float) -> int:
        """Wait up to `timeout` seconds for running jobs; return how many are still running.

        Called when the server stops (e.g. a deploy), so jobs write their results instead of
        being cancelled half-way.
        """
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        if not tasks:
            return 0
        log.warning("Stopping: waiting up to %.0fs for %d running job(s) to finish…", timeout, len(tasks))
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            log.warning("%d job(s) still running after %.0fs; they will be cancelled.", len(pending), timeout)
        else:
            log.warning("All jobs finished.")
        return len(pending)
