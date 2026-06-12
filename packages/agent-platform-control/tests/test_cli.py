"""CLI argparse tests — no real uvicorn boot."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_platform_control import cli


def test_db_init_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_PLATFORM_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")
    from agent_platform_control import config as cfg

    cfg.get_settings.cache_clear()

    rc = cli.main(["db", "init"])
    assert rc == 0
    assert "ok" in capsys.readouterr().out
    assert (tmp_path / "x.db").exists()


def test_serve_dispatches_to_uvicorn():
    with patch("agent_platform_control.cli.uvicorn.run") as run:
        rc = cli.main(["serve", "--port", "9999", "--reload"])
    assert rc == 0
    run.assert_called_once()
    _args, kwargs = run.call_args
    assert kwargs["port"] == 9999
    assert kwargs["reload"] is True


def test_serve_disables_uvicorn_text_access_log():
    """#223: the request-id middleware owns the access line (JSON, with
    request_id). uvicorn's own text access logger (propagate=False, immune to
    setup_logging) would duplicate every request in a second format."""
    with patch("agent_platform_control.cli.uvicorn.run") as run:
        cli.main(["serve"])
    _args, kwargs = run.call_args
    assert kwargs["access_log"] is False


def test_unknown_cmd_exits():
    with pytest.raises(SystemExit):
        cli.main(["nope"])
