"""qcoder (Qwen Code) adapter — OpenAI-protocol on LiteLLM gateway."""

from __future__ import annotations

import shutil

from ..protocol import AgentLaunchSpec, UserContext


class QcoderAdapter:
    name = "qcoder"
    default_model = "qwen-coder-32b"

    def launch_spec(self, ctx: UserContext, prompt: str) -> AgentLaunchSpec:
        model = ctx.model or self.default_model
        # NOTE: qwen-code's non-interactive mode takes the prompt as the value
        # of `-p/--prompt` (`qwen -p "<text>"`); it has no documented stdin
        # sentinel (unlike claude `-p` / `goose run -i -`). So the prompt stays
        # on argv here. Revisit if a future qwen build adds a stdin prompt mode
        # — then switch to stdin_data like the claude/goose adapters.
        return AgentLaunchSpec(
            name=self.name,
            binary="qwen",
            args=("--model", model, "-p", prompt),
            env={
                "OPENAI_BASE_URL": f"{ctx.llm_gateway_url}/v1",
                "OPENAI_API_KEY": ctx.api_key,
            },
            cwd=ctx.workspace,
        )

    def health_check(self, ctx: UserContext) -> tuple[bool, str]:
        if not shutil.which("qwen"):
            return False, "binary 'qwen' not on PATH — see eval/agents/qcoder/install.md"
        return True, "ok"
