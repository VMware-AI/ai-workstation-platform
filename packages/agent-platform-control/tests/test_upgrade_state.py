"""Pure-function tests for the blue-green upgrade state machine."""

from __future__ import annotations

import pytest
from agent_platform_control.orchestrator import upgrade_state as us
from agent_platform_control.orchestrator.upgrade_state import InvalidTransitionError, UpgradeState

# ---------- construction ----------


def test_rejects_unknown_state():
    with pytest.raises(ValueError, match="unknown upgrade state"):
        UpgradeState(value="not-a-real-state")


def test_state_is_frozen():
    from dataclasses import FrozenInstanceError

    st = UpgradeState(value=us.PLANNED)
    with pytest.raises(FrozenInstanceError):
        st.value = us.FAILED  # type: ignore[misc]


# ---------- happy path ----------


def test_happy_path_advance_until_blue_ready():
    st = UpgradeState(value=us.PLANNED)
    st = st.advance()
    assert st.value == us.PROVISIONING_BLUE
    st = st.advance()
    assert st.value == us.HOME_VOLUME_ATTACHING
    st = st.advance()
    assert st.value == us.BLUE_READY


def test_blue_ready_does_not_auto_advance():
    """blue_ready requires explicit cutover — no silent traffic flip."""
    st = UpgradeState(value=us.BLUE_READY)
    with pytest.raises(InvalidTransitionError, match="no automatic edge"):
        st.advance()


def test_full_happy_path_to_completed():
    st = UpgradeState(value=us.PLANNED)
    for _ in range(3):
        st = st.advance()
    assert st.value == us.BLUE_READY
    st = st.cutover()
    assert st.value == us.CUTOVER_IN_PROGRESS
    st = st.advance()
    assert st.value == us.CUTOVER_DONE
    st = st.cleanup()
    assert st.value == us.CLEANUP_PENDING
    st = st.advance()
    assert st.value == us.COMPLETED
    assert st.is_terminal


# ---------- cutover gating ----------


@pytest.mark.parametrize(
    "state",
    [
        us.PLANNED,
        us.PROVISIONING_BLUE,
        us.HOME_VOLUME_ATTACHING,
        us.CUTOVER_IN_PROGRESS,
        us.CUTOVER_DONE,
        us.CLEANUP_PENDING,
        us.COMPLETED,
        us.FAILED,
        us.ROLLED_BACK,
    ],
)
def test_cutover_rejected_from_non_blue_ready(state):
    st = UpgradeState(value=state)
    with pytest.raises(InvalidTransitionError):
        st.cutover()


def test_cutover_allowed_only_from_blue_ready():
    assert UpgradeState(value=us.BLUE_READY).can_cutover
    assert not UpgradeState(value=us.PLANNED).can_cutover
    assert not UpgradeState(value=us.CUTOVER_DONE).can_cutover


# ---------- rollback boundary ----------


@pytest.mark.parametrize(
    "state",
    [us.PLANNED, us.PROVISIONING_BLUE, us.HOME_VOLUME_ATTACHING, us.BLUE_READY],
)
def test_rollback_safe_before_cutover(state):
    st = UpgradeState(value=state)
    assert st.can_rollback
    rolled = st.rollback()
    assert rolled.value == us.ROLLED_BACK
    assert rolled.is_terminal


@pytest.mark.parametrize(
    "state",
    [
        us.CUTOVER_IN_PROGRESS,
        us.CUTOVER_DONE,
        us.CLEANUP_PENDING,
        us.COMPLETED,
        us.FAILED,
        us.ROLLED_BACK,
    ],
)
def test_rollback_blocked_at_or_after_cutover(state):
    st = UpgradeState(value=state)
    assert not st.can_rollback
    with pytest.raises(InvalidTransitionError, match="cutover"):
        st.rollback()


# ---------- cleanup gating ----------


def test_cleanup_only_from_cutover_done():
    assert UpgradeState(value=us.CUTOVER_DONE).can_cleanup
    assert UpgradeState(value=us.CUTOVER_DONE).cleanup().value == us.CLEANUP_PENDING

    for state in (us.PLANNED, us.BLUE_READY, us.CLEANUP_PENDING, us.COMPLETED):
        with pytest.raises(InvalidTransitionError):
            UpgradeState(value=state).cleanup()


# ---------- failure ----------


@pytest.mark.parametrize(
    "state",
    [
        us.PLANNED,
        us.PROVISIONING_BLUE,
        us.HOME_VOLUME_ATTACHING,
        us.BLUE_READY,
        us.CUTOVER_IN_PROGRESS,
        us.CUTOVER_DONE,
        us.CLEANUP_PENDING,
    ],
)
def test_fail_from_any_live_state(state):
    st = UpgradeState(value=state)
    failed = st.fail("vCenter timeout")
    assert failed.value == us.FAILED
    assert failed.is_terminal


@pytest.mark.parametrize("state", [us.COMPLETED, us.FAILED, us.ROLLED_BACK])
def test_fail_rejected_from_terminal(state):
    with pytest.raises(InvalidTransitionError, match="terminal"):
        UpgradeState(value=state).fail()


# ---------- terminal advance ----------


@pytest.mark.parametrize("state", [us.COMPLETED, us.FAILED, us.ROLLED_BACK])
def test_terminal_states_cannot_advance(state):
    with pytest.raises(InvalidTransitionError):
        UpgradeState(value=state).advance()


# ---------- immutability invariant ----------


def test_advance_returns_new_instance():
    original = UpgradeState(value=us.PLANNED)
    advanced = original.advance()
    assert original.value == us.PLANNED  # untouched
    assert advanced.value == us.PROVISIONING_BLUE
    assert original is not advanced


def test_invalid_transition_carries_context():
    try:
        UpgradeState(value=us.PLANNED).cutover()
    except InvalidTransitionError as exc:
        assert exc.current == us.PLANNED
        assert exc.target == us.CUTOVER_IN_PROGRESS
    else:
        pytest.fail("expected InvalidTransitionError")
