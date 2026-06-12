"""Auto-register the M1 adapters on import.

Default-install agents are xiaoguai (preferred) + goose; qcoder / claude-code
remain available but are no longer default. Registered preferred-first.
"""

from __future__ import annotations

from ..registry import register_agent
from .claude_code import ClaudeCodeAdapter
from .goose import GooseAdapter
from .qcoder import QcoderAdapter
from .xiaoguai import XiaoguaiAdapter

register_agent(XiaoguaiAdapter())
register_agent(GooseAdapter())
register_agent(QcoderAdapter())
register_agent(ClaudeCodeAdapter())

__all__ = [
    "ClaudeCodeAdapter",
    "GooseAdapter",
    "QcoderAdapter",
    "XiaoguaiAdapter",
]
