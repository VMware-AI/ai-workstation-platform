"""Deterministic provisioner for tests.

Returns success for every spec by default. Tests can inject a failure
predicate to simulate partial-batch failures.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable

from .protocol import CloneResult, CloneSpec, Provisioner

FailurePredicate = Callable[[CloneSpec], str | None]


class FakeProvisioner(Provisioner):
    """In-memory provisioner. Each call sleeps `delay_s` (default 0) then
    consults `fail_when` — if it returns a string, that becomes the error
    message; if None, the clone succeeds.
    """

    def __init__(
        self,
        delay_s: float = 0.0,
        fail_when: FailurePredicate | None = None,
    ) -> None:
        self._delay_s = delay_s
        self._fail_when = fail_when or (lambda _spec: None)
        self.calls: list[CloneSpec] = []
        # Records destroy_vm targets for test assertions (decision 5 PR-D).
        self.destroyed: list[str] = []

    async def clone_vm(self, spec: CloneSpec) -> CloneResult:
        self.calls.append(spec)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        err = self._fail_when(spec)
        if err is not None:
            return CloneResult(success=False, error=err)
        return CloneResult(
            success=True,
            vm_id=f"fake-{uuid.uuid4().hex[:12]}",
            ip_address="10.0.0.1",
        )

    async def destroy_vm(self, vm_id: str) -> None:
        """Test recorder — appends to ``destroyed`` so tests can assert."""
        self.destroyed.append(vm_id)
