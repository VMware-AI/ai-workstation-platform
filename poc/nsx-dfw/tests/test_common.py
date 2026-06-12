"""Unit tests for _common.py (NsxClient) — mocks requests, no real NSX."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import NsxApiError, NsxClient, NsxConfig  # noqa: E402


def _cfg() -> NsxConfig:
    return NsxConfig(host="nsx.test", user="u", password="p", verify_ssl=False)


def _mock_resp(status: int, json_body: dict | None = None, text: str = ""):
    r = Mock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json = Mock(return_value=json_body or {})
    r.text = text
    r.content = (json_body and b"{}") or b""
    return r


def test_config_validate_missing_host():
    with pytest.raises(ValueError, match="NSX_HOST"):
        NsxConfig(host="", user="u", password="p", verify_ssl=False).validate()


def test_config_validate_missing_creds():
    with pytest.raises(ValueError, match="NSX_USER"):
        NsxConfig(host="x", user="", password="", verify_ssl=False).validate()


def test_get_success():
    with patch("_common.requests.Session.get") as g:
        g.return_value = _mock_resp(200, {"results": [{"id": "x"}]})
        c = NsxClient(_cfg())
        data = c.get("/infra/segments")
        assert data["results"][0]["id"] == "x"
        g.assert_called_once_with("https://nsx.test/policy/api/v1/infra/segments", timeout=30)


def test_get_raises_on_error():
    with patch("_common.requests.Session.get") as g:
        g.return_value = _mock_resp(403, text="forbidden")
        c = NsxClient(_cfg())
        with pytest.raises(NsxApiError) as ei:
            c.get("/x")
        assert ei.value.status == 403


def test_exists_true():
    with patch("_common.requests.Session.get") as g:
        g.return_value = _mock_resp(200, {"id": "x"})
        c = NsxClient(_cfg())
        assert c.exists("/x") is True


def test_exists_false_on_404():
    with patch("_common.requests.Session.get") as g:
        g.return_value = _mock_resp(404, text="not found")
        c = NsxClient(_cfg())
        assert c.exists("/x") is False


def test_exists_reraises_other_errors():
    with patch("_common.requests.Session.get") as g:
        g.return_value = _mock_resp(500, text="boom")
        c = NsxClient(_cfg())
        with pytest.raises(NsxApiError):
            c.exists("/x")


def test_dry_run_skips_patch(capsys):
    with patch.dict("os.environ", {"AGENT_PLATFORM_NSX_DRY_RUN": "1"}):
        c = NsxClient(_cfg())
        result = c.patch("/x", {"foo": "bar"})
    assert result == {"dry_run": True}
    captured = capsys.readouterr()
    assert "[DRY] PATCH /x" in captured.out


def test_dry_run_skips_delete(capsys):
    with patch.dict("os.environ", {"AGENT_PLATFORM_NSX_DRY_RUN": "1"}):
        c = NsxClient(_cfg())
        c.delete("/x")
    assert "[DRY] DELETE /x" in capsys.readouterr().out


def test_delete_404_is_idempotent():
    with patch("_common.requests.Session.delete") as d:
        d.return_value = _mock_resp(404, text="gone")
        c = NsxClient(_cfg())
        c.delete("/x")  # should not raise
