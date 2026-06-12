"""Blue-green upgrade state machine.

Models the lifecycle of one Upgrade aggregate (one row in ``upgrades`` table).

Lifecycle (happy path)::

    planned
       │
       ▼
    provisioning_blue        (clone new VMs from new image_version)
       │
       ▼
    home_volume_attaching    (copy /home volumes from green to blue — non-destructive)
       │
       ▼
    blue_ready               (blue side healthy; portal can show "next-login switch" hint)
       │  cutover
       ▼
    cutover_in_progress      (flip routing / mark VMs primary)
       │
       ▼
    cutover_done             (point of no return for one-click rollback)
       │  cleanup
       ▼
    cleanup_pending          (green VMs cold-stored for 7 days)
       │
       ▼
    completed

Branches::

    *  --fail-->  failed        (terminal; manual cleanup)
    planned..blue_ready --rollback--> rolled_back   (still on green, blue VMs torn down)

Design notes:
  - States are strings rather than an Enum so they serialize cleanly through
    the DB (``upgrades.state`` column) and JSON responses without converters.
  - ``UpgradeState`` is ``frozen=True`` per repo coding-style rules — every
    transition returns a NEW instance. Worker / API code must reassign.
  - The "point of no return" is ``cutover_done``: once we've sent users to the
    blue VMs, ``rollback`` is no longer safe (their session state lives on
    blue). The skeleton enforces this at ``can_rollback``.
  - We deliberately do NOT support rollback from ``cleanup_pending`` /
    ``completed`` — green VMs may be gone or in cold storage. Use a fresh
    Upgrade (v0.2 → v0.1) instead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# ---------- state constants ----------

PLANNED = "planned"
PROVISIONING_BLUE = "provisioning_blue"
HOME_VOLUME_ATTACHING = "home_volume_attaching"
BLUE_READY = "blue_ready"
CUTOVER_IN_PROGRESS = "cutover_in_progress"
CUTOVER_DONE = "cutover_done"
CLEANUP_PENDING = "cleanup_pending"
COMPLETED = "completed"
FAILED = "failed"
ROLLED_BACK = "rolled_back"

ALL_STATES: frozenset[str] = frozenset(
    {
        PLANNED,
        PROVISIONING_BLUE,
        HOME_VOLUME_ATTACHING,
        BLUE_READY,
        CUTOVER_IN_PROGRESS,
        CUTOVER_DONE,
        CLEANUP_PENDING,
        COMPLETED,
        FAILED,
        ROLLED_BACK,
    }
)

TERMINAL_STATES: frozenset[str] = frozenset({COMPLETED, FAILED, ROLLED_BACK})

# States from which one-click rollback is still safe (no user traffic on blue yet).
ROLLBACK_SAFE_STATES: frozenset[str] = frozenset(
    {PLANNED, PROVISIONING_BLUE, HOME_VOLUME_ATTACHING, BLUE_READY}
)

# Forward-progress edges driven by the worker (not user-triggered).
_AUTO_EDGES: dict[str, str] = {
    PLANNED: PROVISIONING_BLUE,
    PROVISIONING_BLUE: HOME_VOLUME_ATTACHING,
    HOME_VOLUME_ATTACHING: BLUE_READY,
    CUTOVER_IN_PROGRESS: CUTOVER_DONE,
    CUTOVER_DONE: CLEANUP_PENDING,
    CLEANUP_PENDING: COMPLETED,
}


# ---------- error type ----------


class InvalidTransitionError(ValueError):
    """Raised when a transition is not legal for the current state.

    Carries the offending state + attempted target so the caller can build a
    teaching-error response (e.g. HTTP 409 with hint).
    """

    def __init__(self, current: str, target: str, hint: str = "") -> None:
        msg = f"cannot transition from {current!r} to {target!r}"
        if hint:
            msg = f"{msg}: {hint}"
        super().__init__(msg)
        self.current = current
        self.target = target


# ---------- the value object ----------


@dataclass(frozen=True)
class UpgradeState:
    """Immutable snapshot of one upgrade's state.

    All ``advance_*`` / ``mark_*`` methods return a NEW UpgradeState; the
    original is never mutated. Callers reassign::

        st = UpgradeState(value=PLANNED)
        st = st.advance()              # provisioning_blue
        st = st.fail("vCenter timeout")  # failed
    """

    value: str

    def __post_init__(self) -> None:
        if self.value not in ALL_STATES:
            raise ValueError(f"unknown upgrade state: {self.value!r}")

    # ---- predicates ----

    @property
    def is_terminal(self) -> bool:
        return self.value in TERMINAL_STATES

    @property
    def can_rollback(self) -> bool:
        """Rollback is safe only before user traffic moves to blue."""
        return self.value in ROLLBACK_SAFE_STATES

    @property
    def can_cutover(self) -> bool:
        return self.value == BLUE_READY

    @property
    def can_cleanup(self) -> bool:
        return self.value == CUTOVER_DONE

    # ---- transitions (return a new instance) ----

    def advance(self) -> UpgradeState:
        """Forward-progress edge driven by the worker.

        Raises ``InvalidTransitionError`` if there is no auto-edge from ``value``
        (e.g. ``blue_ready`` requires explicit ``cutover()``; terminal states
        cannot advance).
        """
        target = _AUTO_EDGES.get(self.value)
        if target is None:
            raise InvalidTransitionError(
                self.value,
                "<auto>",
                hint="no automatic edge; this state requires explicit cutover/rollback/cleanup",
            )
        return replace(self, value=target)

    def cutover(self) -> UpgradeState:
        """User-triggered: start flipping traffic to blue."""
        if not self.can_cutover:
            raise InvalidTransitionError(
                self.value,
                CUTOVER_IN_PROGRESS,
                hint=f"cutover requires state={BLUE_READY!r}",
            )
        return replace(self, value=CUTOVER_IN_PROGRESS)

    def rollback(self) -> UpgradeState:
        """User-triggered: tear down blue side, stay on green.

        Only legal before ``cutover_done`` — once users are on blue, their
        session state lives there and rolling back loses work.
        """
        if not self.can_rollback:
            raise InvalidTransitionError(
                self.value,
                ROLLED_BACK,
                hint=(
                    "rollback is only safe before cutover_done; "
                    "after cutover, start a new upgrade in the reverse direction"
                ),
            )
        return replace(self, value=ROLLED_BACK)

    def cleanup(self) -> UpgradeState:
        """User-triggered: start deleting green VMs (7-day cold storage handled elsewhere)."""
        if not self.can_cleanup:
            raise InvalidTransitionError(
                self.value,
                CLEANUP_PENDING,
                hint=f"cleanup requires state={CUTOVER_DONE!r}",
            )
        return replace(self, value=CLEANUP_PENDING)

    def fail(self, reason: str = "") -> UpgradeState:
        """Mark as failed. Legal from any non-terminal state."""
        if self.is_terminal:
            raise InvalidTransitionError(self.value, FAILED, hint="already terminal")
        # ``reason`` is intentionally not stored here — Upgrade row tracks it
        # in extra/plan_json. Keeping the value object small.
        del reason
        return replace(self, value=FAILED)
