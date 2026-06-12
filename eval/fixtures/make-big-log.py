#!/usr/bin/env python3
# ruff: noqa: S311 — fixture generator; randomness is cosmetic, not crypto
"""Generate ~5000 lines of fake nginx access log for p19.

Used at fixture build time (not at runner time — keeps repo small).
Output: eval/fixtures/big_log.txt
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

random.seed(20260517)

PATHS_OK = [
    "/api/users",
    "/api/users/me",
    "/api/orders",
    "/api/orders/list",
    "/api/products",
    "/api/products/search",
    "/api/auth/login",
    "/api/auth/logout",
    "/health",
    "/static/app.js",
    "/static/style.css",
]
PATHS_5XX = [
    "/api/payment/charge",
    "/api/report/export",
    "/api/admin/audit",
    "/api/llm/chat",
    "/api/files/upload",
]
STATUSES = (
    [200] * 80
    + [201] * 5
    + [301] * 3
    + [304] * 3
    + [400] * 2
    + [401] * 2
    + [403] * 1
    + [404] * 2
    + [500] * 1
    + [502] * 1
)
METHODS = ["GET"] * 80 + ["POST"] * 15 + ["PUT"] * 3 + ["DELETE"] * 2

start = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)


def line(i: int) -> str:
    ts = start + timedelta(seconds=i * 17)
    method = random.choice(METHODS)
    status = random.choice(STATUSES)
    path = random.choice(PATHS_5XX) if status >= 500 else random.choice(PATHS_OK)
    if random.random() < 0.3:
        path = path + "?id=" + str(random.randint(1, 9999))
    size = random.randint(120, 5000)
    ip = f"10.20.{random.randint(1, 50)}.{random.randint(1, 254)}"
    ua = random.choice(
        [
            "Mozilla/5.0",
            "curl/8.4.0",
            "Go-http-client/1.1",
            "python-requests/2.32",
        ]
    )
    stamp = ts.strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'{ip} - - [{stamp}] "{method} {path} HTTP/1.1" {status} {size} "-" "{ua}"\n'


def main() -> None:
    out = Path(__file__).parent / "big_log.txt"
    with out.open("w") as f:
        for i in range(5000):
            f.write(line(i))
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
