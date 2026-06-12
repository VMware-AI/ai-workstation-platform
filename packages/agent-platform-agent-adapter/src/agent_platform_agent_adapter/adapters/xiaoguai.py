"""xiaoguai (小怪) adapter — OpenAI-compat provider on the LiteLLM gateway.

Upstream uses the standard OpenAI env vars (verified against xiaoguai's
docs/user-guide/local-experiment.md + .env.local.example): OPENAI_API_KEY +
OPENAI_BASE_URL (with the /v1 suffix). The model is passed as the `--model` arg
(upstream also accepts OPENAI_MODEL, but we pass it explicitly). One-shot prompts
run via `xiaoguai chat --prompt <p>` (its `serve` command is the web server,
unused here).
"""

from __future__ import annotations

import shutil

from ..protocol import AgentLaunchSpec, UserContext


class XiaoguaiAdapter:
    name = "xiaoguai"
    default_model = "qwen-coder-32b"

    def launch_spec(self, ctx: UserContext, prompt: str) -> AgentLaunchSpec:
        model = ctx.model or self.default_model
        # NOTE: `xiaoguai chat` takes the prompt as the value of `--prompt`;
        # it has no documented stdin sentinel (unlike claude `-p` /
        # `goose run -i -`). So the prompt stays on argv here. Revisit if a
        # future xiaoguai build adds a stdin prompt mode — then switch to
        # stdin_data like the claude/goose adapters.
        return AgentLaunchSpec(
            name=self.name,
            binary="xiaoguai",
            args=("chat", "--prompt", prompt, "--model", model),
            env={
                # xiaoguai's OpenAI-compat client expects the /v1 suffix
                # (default base is https://api.openai.com/v1).
                "OPENAI_BASE_URL": f"{ctx.llm_gateway_url}/v1",
                "OPENAI_API_KEY": ctx.api_key,
            },
            cwd=ctx.workspace,
        )

    def health_check(self, ctx: UserContext) -> tuple[bool, str]:
        if not shutil.which("xiaoguai"):
            return False, "binary 'xiaoguai' not on PATH — pip install xiaoguai"
        return True, "ok"
