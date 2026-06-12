# goose — agent container image

> Block goose AI agent, packaged for AI Workstation Platform per-user VMs.

This image wraps the official upstream Linux release of [block/goose](https://github.com/block/goose)
(now hosted at [aaif-goose/goose](https://github.com/aaif-goose/goose)) and adds a thin entrypoint
that reads platform-provided env vars and execs `goose`.

## Image tags

Published to `ghcr.io/vmware-ai/ai-workstation-platform/goose`:

| Tag | Meaning |
|---|---|
| `1.37.0` | Pinned to a specific upstream goose release |
| `latest` | Last build from `main` (currently 1.37.0) |
| `sha-<7>` | Commit SHA of the platform repo at build time |

Built for `linux/amd64` + `linux/arm64`.

## Required env

| Var | Meaning |
|---|---|
| `OPENAI_HOST` | LiteLLM gateway URL — e.g. `http://agent-platform-llm-gateway:4000` |
| `OPENAI_API_KEY` | Per-user token issued by C1 / C5 |

## Optional env (with image defaults)

| Var | Default | Meaning |
|---|---|---|
| `GOOSE_PROVIDER` | `openai` | Provider string consumed by goose |
| `GOOSE_MODEL` | `qwen-coder-32b` | Model name routed by LiteLLM |
| `XDG_CONFIG_HOME` | `/home/goose/.config` | Where goose reads its config file |

## Usage

### One-shot (CI / batch)

```bash
docker run --rm \
  -e OPENAI_HOST=http://agent-platform-llm-gateway:4000 \
  -e OPENAI_API_KEY=$AGENT_PLATFORM_TOKEN \
  -v "$PWD:/workspace" \
  ghcr.io/vmware-ai/ai-workstation-platform/goose:latest \
  run -t "summarize README.md" --quiet
```

### Interactive session (user VM)

```bash
docker run -it \
  -e OPENAI_HOST=http://agent-platform-llm-gateway:4000 \
  -e OPENAI_API_KEY=$AGENT_PLATFORM_TOKEN \
  -v "$HOME/workspace:/workspace" \
  --name goose \
  ghcr.io/vmware-ai/ai-workstation-platform/goose:latest
```

### Tarball fallback (offline / no-docker host)

If the customer environment cannot run docker, extract the upstream tarball directly:

```bash
GOOSE_VERSION=v1.37.0
curl -fsSL "https://github.com/block/goose/releases/download/${GOOSE_VERSION}/goose-x86_64-unknown-linux-gnu.tar.gz" \
  | tar -xz -C /opt/goose
ln -s /opt/goose/goose /usr/local/bin/goose
```

Then set the same env vars and run `goose` directly. cloud-init in C3 golden image
will detect docker availability and pick the right path.

## Local development

```bash
cd packages/agent-platform-image/agents/goose
docker build -t goose:dev --build-arg GOOSE_VERSION=v1.37.0 .
docker run --rm --entrypoint goose goose:dev --version
```

## Updating to a new upstream release

Current scope ships **a single pinned version**. To bump:

1. Bump `DEFAULT_GOOSE_VERSION` in `.github/workflows/goose-image.yml`
2. Bump `ARG GOOSE_VERSION=` in this directory's `Dockerfile`
3. Open PR — CI smoke-tests the build on amd64
4. After merge, the workflow rebuilds and publishes the new tag

## Mirroring to a customer's local registry

VM-side cloud-init pulls from the **customer's local registry**, not GHCR, in
production. To seed a customer mirror:

```bash
# On a host with both registries reachable:
docker pull ghcr.io/vmware-ai/ai-workstation-platform/goose:1.37.0
docker tag  ghcr.io/vmware-ai/ai-workstation-platform/goose:1.37.0 \
            registry.customer.internal/agent-platform/goose:1.37.0
docker push registry.customer.internal/agent-platform/goose:1.37.0
```

The cloud-init template in C3 takes the registry URL as a parameter (see
the C3 cloud-init PR for `REGISTRY_URL` / `IMAGE_TAG` knobs).
