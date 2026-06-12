from __future__ import annotations

import pytest
from agent_platform_approval import InvalidTransitionError, State, transition


@pytest.mark.unit
@pytest.mark.parametrize(
    "src,dst",
    [(State.PENDING, State.APPROVED), (State.PENDING, State.REJECTED)],
)
def test_allowed_transitions(src: State, dst: State) -> None:
    assert transition(src, dst) == dst


@pytest.mark.unit
@pytest.mark.parametrize(
    "src,dst",
    [
        (State.APPROVED, State.REJECTED),
        (State.REJECTED, State.APPROVED),
        (State.APPROVED, State.PENDING),
        (State.REJECTED, State.PENDING),
        (State.PENDING, State.PENDING),  # identity is intentionally rejected
        (State.APPROVED, State.APPROVED),
        (State.REJECTED, State.REJECTED),
    ],
)
def test_forbidden_transitions(src: State, dst: State) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(src, dst)


@pytest.mark.unit
def test_terminal_error_message_lists_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error must teach the caller what *is* allowed."""
    with pytest.raises(InvalidTransitionError, match="terminal"):
        transition(State.APPROVED, State.REJECTED)
    with pytest.raises(InvalidTransitionError, match="'approved', 'rejected'"):
        transition(State.PENDING, State.PENDING)
