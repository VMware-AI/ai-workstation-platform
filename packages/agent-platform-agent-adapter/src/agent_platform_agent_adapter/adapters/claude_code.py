"""Claude Code adapter — Anthropic protocol via LiteLLM passthrough."""

from __future__ import annotations

import shutil

from ..protocol import AgentLaunchSpec, UserContext


class ClaudeCodeAdapter:
    name = "claude-code"
    # Anthropic protocol; LiteLLM routes claude-3-5-sonnet → local qwen.
    default_model = "claude-3-5-sonnet-20240620"

    def launch_spec(self, ctx: UserContext, prompt: str) -> AgentLaunchSpec:
        model = ctx.model or self.default_model
        # Claude Code reads the prompt from stdin when `-p`/`--print` is
        # given with no inline prompt arg (`echo "..." | claude -p`). Feeding
        # it via stdin keeps it off argv — out of `ps`/`/proc` and clear of
        # ARG_MAX for long prompts.
        return AgentLaunchSpec(
            name=self.name,
            binary="claude",
            args=("-p", "--model", model),
            env={
                "ANTHROPIC_BASE_URL": f"{ctx.llm_gateway_url}/anthropic",
                "ANTHROPIC_API_KEY": ctx.api_key,
            },
            cwd=ctx.workspace,
            stdin_data=prompt,
        )

    def health_check(self, ctx: UserContext) -> tuple[bool, str]:
        if not shutil.which("claude"):
            return (
                False,
                "binary 'claude' not on PATH — "
                "see eval/agents/claude-code/install.md (license: #34)",
            )
        return True, "ok"
