"""Goose (Block) adapter — OpenAI provider on LiteLLM gateway."""

from __future__ import annotations

import shutil

from ..protocol import AgentLaunchSpec, UserContext


class GooseAdapter:
    name = "goose"
    default_model = "qwen-coder-32b"

    def launch_spec(self, ctx: UserContext, prompt: str) -> AgentLaunchSpec:
        model = ctx.model or self.default_model
        # Prompt stays on argv via `goose run -t <prompt>` — the form the
        # repo's own eval runner (eval/runner.py) and install docs use, and
        # the only one verified to work. goose has no confirmed stdin prompt
        # mode (install.md's FAQ warns bare stdin hangs), so unlike claude
        # `-p` we do NOT pipe the prompt; the ARG_MAX / ps-hiding win isn't
        # available here until a stdin path is verified upstream.
        return AgentLaunchSpec(
            name=self.name,
            binary="goose",
            args=("run", "-t", prompt, "--quiet"),
            env={
                "GOOSE_PROVIDER": "openai",
                "OPENAI_HOST": ctx.llm_gateway_url,
                "OPENAI_API_KEY": ctx.api_key,
                "GOOSE_MODEL": model,
            },
            cwd=ctx.workspace,
        )

    def health_check(self, ctx: UserContext) -> tuple[bool, str]:
        if not shutil.which("goose"):
            return False, "binary 'goose' not on PATH — see eval/agents/goose/install.md"
        return True, "ok"
