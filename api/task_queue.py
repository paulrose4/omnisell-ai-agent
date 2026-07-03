from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class TaskRecord:
    task_id: str
    queue_name: str
    action: str
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    request_id: str | None = None
    result: Any | None = None
    error: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "queue_name": self.queue_name,
            "action": self.action,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "request_id": self.request_id,
            "result": self.result,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass
class QueuedTask:
    record: TaskRecord
    handler: Callable[[], Any]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.requests_total = 0
        self.requests_failed = 0
        self.request_latency_ms: deque[float] = deque(maxlen=500)
        self.tasks_total = 0
        self.tasks_succeeded = 0
        self.tasks_failed = 0
        self.task_latency_by_action: dict[str, deque[float]] = {}
        self.request_count_by_path: dict[str, int] = {}
        self.error_count_by_path: dict[str, int] = {}

    async def record_request(self, path: str, status_code: int, latency_ms: float) -> None:
        async with self._lock:
            self.requests_total += 1
            self.request_latency_ms.append(latency_ms)
            self.request_count_by_path[path] = self.request_count_by_path.get(path, 0) + 1
            if status_code >= 500:
                self.requests_failed += 1
                self.error_count_by_path[path] = self.error_count_by_path.get(path, 0) + 1

    async def record_task_start(self) -> None:
        async with self._lock:
            self.tasks_total += 1

    async def record_task_finish(self, action: str, status: TaskStatus, latency_ms: float) -> None:
        async with self._lock:
            if status == TaskStatus.SUCCEEDED:
                self.tasks_succeeded += 1
            elif status == TaskStatus.FAILED:
                self.tasks_failed += 1
            bucket = self.task_latency_by_action.setdefault(action, deque(maxlen=200))
            bucket.append(latency_ms)

    @staticmethod
    def _avg(values: deque[float]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 2)

    async def snapshot(self, queue_lengths: dict[str, int] | None = None) -> dict[str, Any]:
        async with self._lock:
            return {
                "requests_total": self.requests_total,
                "requests_failed": self.requests_failed,
                "request_avg_latency_ms": self._avg(self.request_latency_ms),
                "tasks_total": self.tasks_total,
                "tasks_succeeded": self.tasks_succeeded,
                "tasks_failed": self.tasks_failed,
                "task_avg_latency_ms_by_action": {
                    action: self._avg(values) for action, values in self.task_latency_by_action.items()
                },
                "request_count_by_path": dict(self.request_count_by_path),
                "error_count_by_path": dict(self.error_count_by_path),
                "queue_lengths": queue_lengths or {},
            }


class InMemoryTaskQueue:
    def __init__(self, metrics: MetricsRegistry, workers: dict[str, int] | None = None) -> None:
        self.metrics = metrics
        self.workers = workers or {"text": 2, "vision": 1}
        self.queues: dict[str, asyncio.Queue[QueuedTask]] = {name: asyncio.Queue() for name in self.workers}
        self.tasks: dict[str, TaskRecord] = {}
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for queue_name, worker_count in self.workers.items():
            for worker_index in range(worker_count):
                self._worker_tasks.append(asyncio.create_task(self._worker_loop(queue_name, worker_index)))

    async def shutdown(self) -> None:
        for worker_task in self._worker_tasks:
            worker_task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        self._started = False

    def enqueue(
        self,
        queue_name: str,
        action: str,
        handler: Callable[[], Any],
        request_id: str | None = None,
    ) -> TaskRecord:
        if queue_name not in self.queues:
            raise ValueError(f"Unknown queue: {queue_name}")
        task_id = str(uuid.uuid4())
        record = TaskRecord(task_id=task_id, queue_name=queue_name, action=action, request_id=request_id)
        self.tasks[task_id] = record
        self.queues[queue_name].put_nowait(QueuedTask(record=record, handler=handler))
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def list_tasks(self, limit: int = 50, status: TaskStatus | None = None) -> list[TaskRecord]:
        records = sorted(self.tasks.values(), key=lambda item: item.created_at, reverse=True)
        if status is not None:
            records = [record for record in records if record.status == status]
        return records[:limit]

    def queue_lengths(self) -> dict[str, int]:
        return {name: queue.qsize() for name, queue in self.queues.items()}

    async def _worker_loop(self, queue_name: str, worker_index: int) -> None:
        queue = self.queues[queue_name]
        while True:
            queued_task = await queue.get()
            try:
                await self._run_task(queued_task)
            finally:
                queue.task_done()

    async def _run_task(self, queued_task: QueuedTask) -> None:
        record = queued_task.record
        started = time.perf_counter()
        record.status = TaskStatus.RUNNING
        record.started_at = time.time()
        record.updated_at = record.started_at
        await self.metrics.record_task_start()

        try:
            if inspect.iscoroutinefunction(queued_task.handler):
                result = await queued_task.handler()
            else:
                result = await asyncio.to_thread(queued_task.handler)
                if inspect.isawaitable(result):
                    result = await result
            record.result = result
            record.status = TaskStatus.SUCCEEDED
        except Exception as exc:
            record.error = str(exc)
            record.status = TaskStatus.FAILED
        finally:
            record.finished_at = time.time()
            record.updated_at = record.finished_at
            record.latency_ms = round((time.perf_counter() - started) * 1000, 2)
            await self.metrics.record_task_finish(record.action, record.status, record.latency_ms)
