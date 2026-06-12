"""NSX-T REST client — 其他脚本共用。

设计原则：
- 薄封装，不做缓存 / 不做重试（让上层显式控制）
- 所有 GET/POST/PATCH/DELETE 命中 /policy/api/v1
- 失败抛 NsxApiError（含 status + body 摘要），不静默
- 支持 dry-run（环境变量 AGENT_PLATFORM_NSX_DRY_RUN=1）
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class NsxApiError(Exception):
    def __init__(self, method: str, path: str, status: int, body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> {status}: {body[:300]}")


@dataclass(frozen=True)
class NsxConfig:
    host: str
    user: str
    password: str
    verify_ssl: bool

    @classmethod
    def from_env(cls) -> "NsxConfig":
        return cls(
            host=os.environ.get("NSX_HOST", ""),
            user=os.environ.get("NSX_USER", ""),
            password=os.environ.get("NSX_PASSWORD", ""),
            verify_ssl=os.environ.get("NSX_VERIFY_SSL", "false").lower() == "true",
        )

    def validate(self) -> None:
        if not self.host:
            raise ValueError("NSX_HOST not set; copy .env.example and fill")
        if not self.user or not self.password:
            raise ValueError("NSX_USER / NSX_PASSWORD not set")


class NsxClient:
    """Minimal NSX-T Policy API client."""

    def __init__(self, cfg: NsxConfig | None = None):
        self.cfg = cfg or NsxConfig.from_env()
        self.cfg.validate()
        self.base = f"https://{self.cfg.host}/policy/api/v1"
        self.session = requests.Session()
        self.session.auth = (self.cfg.user, self.cfg.password)
        self.session.verify = self.cfg.verify_ssl
        self.session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
        if not self.cfg.verify_ssl:
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
        self.dry_run = os.environ.get("AGENT_PLATFORM_NSX_DRY_RUN", "") == "1"

    def get(self, path: str) -> dict[str, Any]:
        r = self.session.get(f"{self.base}{path}", timeout=30)
        if not r.ok:
            raise NsxApiError("GET", path, r.status_code, r.text)
        return r.json()

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.dry_run:
            print(f"[DRY] PATCH {path}\n{json.dumps(body, indent=2)[:500]}")
            return {"dry_run": True}
        r = self.session.patch(f"{self.base}{path}", json=body, timeout=60)
        if not r.ok:
            raise NsxApiError("PATCH", path, r.status_code, r.text)
        return r.json() if r.content else {}

    def delete(self, path: str) -> None:
        if self.dry_run:
            print(f"[DRY] DELETE {path}")
            return
        r = self.session.delete(f"{self.base}{path}", timeout=30)
        # 404 视为已删；其它非 2xx 抛
        if r.status_code == 404:
            return
        if not r.ok:
            raise NsxApiError("DELETE", path, r.status_code, r.text)

    def exists(self, path: str) -> bool:
        try:
            self.get(path)
            return True
        except NsxApiError as e:
            if e.status == 404:
                return False
            raise
