"""Thin CLI wrapper: `agent-platform-control serve` / `agent-platform-control db {init,migrate}`."""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine

from .config import get_settings
from .db.models import Base


def _serve(args: argparse.Namespace) -> int:
    uvicorn.run(
        "agent_platform_control.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=get_settings().log_level.lower(),
        # #223: the request-id middleware emits the access line (JSON, with
        # request_id). uvicorn's own text access logger has propagate=False,
        # so setup_logging can't reformat it — without this flag every
        # request logs twice in two formats.
        access_log=False,
    )
    return 0


def _db_init(_args: argparse.Namespace) -> int:
    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(run())
    print("ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-platform-control")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="run uvicorn (dev)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=_serve)

    sp = sub.add_parser("db", help="db operations")
    sp_sub = sp.add_subparsers(dest="op", required=True)
    sp_init = sp_sub.add_parser("init", help="create tables (dev only; prod uses Alembic)")
    sp_init.set_defaults(func=_db_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
