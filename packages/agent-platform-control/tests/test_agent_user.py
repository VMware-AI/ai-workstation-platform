"""Tests for orchestrator.agent_user — username sanitization + UID derivation.

Decision 1B: every VM gets a per-owner linux account. UID is derived
deterministically from owner_id (M1 expedient; collisions handled by TODO
to switch to DB-backed allocation once Keycloak / user provisioning lands).
"""

from __future__ import annotations

import pytest
from agent_platform_control.orchestrator.agent_user import (
    UID_MAX,
    UID_MIN,
    AgentUserError,
    derive_uid,
    sanitize_username,
)

# ---------------------------------------------------------------- sanitize


class TestSanitizeUsername:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("alice", "alice"),
            ("Alice", "alice"),
            ("alice123", "alice123"),
            ("user-name", "user-name"),  # hyphen is legal
            ("with_underscore", "with_underscore"),
        ],
    )
    def test_simple_passthrough(self, raw, expected):
        assert sanitize_username(raw) == expected

    def test_uppercase_lowered(self):
        assert sanitize_username("AliceWang") == "alicewang"

    def test_email_like_owner_id(self):
        assert sanitize_username("Alice.Wang@example.com") == "alice_wang_example_com"

    def test_spaces_replaced(self):
        assert sanitize_username("alice wang") == "alice_wang"

    def test_leading_digit_prefixed(self):
        # linux usernames must start with [a-z_], not a digit
        assert sanitize_username("123abc") == "u_123abc"

    def test_non_ascii_replaced(self):
        # CJK characters → underscores; result must still be valid
        result = sanitize_username("alice中文")
        assert result.startswith("alice")
        assert all(c.isascii() for c in result)

    def test_truncated_to_32_chars(self):
        # Linux LOGIN_NAME_MAX is 32 chars; we cap at 31 to be safe
        long_name = "a" * 100
        result = sanitize_username(long_name)
        assert len(result) <= 31

    def test_empty_input_raises(self):
        with pytest.raises(AgentUserError):
            sanitize_username("")

    def test_only_invalid_chars_raises(self):
        # If every character gets stripped to nothing meaningful, fail loud
        with pytest.raises(AgentUserError):
            sanitize_username("@@@")

    def test_result_matches_linux_useradd_regex(self):
        # POSIX-compliant useradd accepts: [a-z_][a-z0-9_-]*
        import re

        pattern = re.compile(r"^[a-z_][a-z0-9_-]{0,30}$")
        for raw in ["alice", "Alice.Wang@x.com", "123user", "user 1", "Bob-Lee"]:
            assert pattern.match(sanitize_username(raw)), f"failed: {raw}"


# ---------------------------------------------------------------- derive_uid


class TestDeriveUid:
    def test_deterministic(self):
        assert derive_uid("alice") == derive_uid("alice")

    def test_in_valid_range(self):
        for owner in ["alice", "bob", "carol", "user-1", "very.long.owner.id@example.com"]:
            uid = derive_uid(owner)
            assert UID_MIN <= uid <= UID_MAX, f"{owner} → {uid} out of range"

    def test_different_owners_usually_different_uids(self):
        # Probabilistic — for a small sample, expect mostly unique
        owners = [f"user{i:04d}" for i in range(20)]
        uids = {derive_uid(o) for o in owners}
        # With 50k slots and 20 owners, P(no collision) is very high
        assert len(uids) >= 19  # allow at most 1 collision

    def test_uid_min_above_1000(self):
        # 0-999 are reserved for system users on most distros; we start at 1000
        assert UID_MIN >= 1000

    def test_uid_max_below_60000(self):
        # 60000+ is reserved for NSS-cached / nobody-style entries on some distros
        assert UID_MAX < 60000

    def test_empty_owner_raises(self):
        with pytest.raises(AgentUserError):
            derive_uid("")

    def test_uid_derives_from_sanitized_input(self):
        # Same logical owner via different raw spellings should map to same UID
        # (because sanitization normalizes them)
        assert derive_uid("Alice") == derive_uid("alice")
        assert derive_uid("Alice.Wang@x.com") == derive_uid("alice.wang@x.com")
