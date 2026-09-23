"""Process-local runtime state exposed by the health endpoint."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RuntimeSnapshot:
    process_instance_id: str
    requests_since_start: int


class ProcessRuntimeState:
    """Track a deployment-verifiable counter for one application instance."""

    def __init__(self) -> None:
        self._process_instance_id = str(uuid.uuid4())
        self._requests_since_start = 0
        self._lock = Lock()

    def record_request(self) -> None:
        with self._lock:
            self._requests_since_start += 1

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                process_instance_id=self._process_instance_id,
                requests_since_start=self._requests_since_start,
            )
