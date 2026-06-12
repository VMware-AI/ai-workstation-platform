#!/usr/bin/env bash
# Goose container entrypoint.
#
# Reads connection env, validates required vars, then execs goose.
#
# Required env:
#   OPENAI_HOST     LiteLLM gateway URL (e.g. http://agent-platform-llm-gateway:4000)
#   OPENAI_API_KEY  Per-user token issued by C1 / C5
#
# Optional env (with Dockerfile defaults):
#   GOOSE_PROVIDER  openai
#   GOOSE_MODEL     qwen-coder-32b
#
# CMD args are forwarded to `goose` verbatim.
# Default CMD = "session" (interactive). Use "run -t <prompt>" for one-shot.

set -euo pipefail

if [[ -z "${OPENAI_HOST}" ]]; then
    echo "FATAL: OPENAI_HOST is required (LiteLLM gateway URL)" >&2
    exit 64
fi

if [[ -z "${OPENAI_API_KEY}" ]]; then
    echo "FATAL: OPENAI_API_KEY is required (per-user token)" >&2
    exit 64
fi

echo "goose: version=$(goose --version 2>/dev/null | head -1)"
# Provider + model are non-sensitive; host (LiteLLM gateway URL) goes to stderr
# only — keeping it out of stdout in case logs are shipped to less-trusted sinks.
echo "goose: provider=${GOOSE_PROVIDER} model=${GOOSE_MODEL}"
echo "goose: host=${OPENAI_HOST}" >&2

exec goose "$@"
