# syntax=docker/dockerfile:1.7

# Goose agent runtime — wraps the official upstream linux release
# https://github.com/aaif-goose/goose (formerly block/goose; old URLs redirect)

ARG GOOSE_VERSION=v1.37.0
ARG TARGETARCH=amd64
# SHA256 of the upstream goose tarballs. Bump together with GOOSE_VERSION.
# Verify with:  shasum -a 256 goose-*-unknown-linux-gnu.tar.gz
ARG GOOSE_SHA256_AMD64=d575073c059f22e0b697f1a0b502ecf629cfd4604a67e7659a8542a4ea7463bc
ARG GOOSE_SHA256_ARM64=a404b1c8b404b568631d85b7ac9dcfdfa7314113269d6a3b60f0869966580974

# ---- Stage 1: fetch + verify ----
FROM debian:bookworm-slim@sha256:0104b334637a5f19aa9c983a91b54c89887c0984081f2068983107a6f6c21eeb AS fetch
ARG GOOSE_VERSION
ARG TARGETARCH
ARG GOOSE_SHA256_AMD64
ARG GOOSE_SHA256_ARM64

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN case "${TARGETARCH}" in \
        amd64) TRIPLE=x86_64-unknown-linux-gnu;  SHA="${GOOSE_SHA256_AMD64}" ;; \
        arm64) TRIPLE=aarch64-unknown-linux-gnu; SHA="${GOOSE_SHA256_ARM64}" ;; \
        *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && URL="https://github.com/aaif-goose/goose/releases/download/${GOOSE_VERSION}/goose-${TRIPLE}.tar.gz" \
    && echo "Downloading ${URL}" \
    && curl -fsSL "${URL}" -o goose.tar.gz \
    && echo "${SHA}  goose.tar.gz" | sha256sum -c - \
    && tar -xzf goose.tar.gz \
    && test -x goose \
    && ./goose --version

# ---- Stage 2: runtime ----
FROM debian:bookworm-slim@sha256:0104b334637a5f19aa9c983a91b54c89887c0984081f2068983107a6f6c21eeb AS runtime
ARG GOOSE_VERSION

LABEL org.opencontainers.image.title="goose" \
      org.opencontainers.image.description="Block goose AI agent — packaged for AI Workstation Platform" \
      org.opencontainers.image.source="https://github.com/VMware-AI/ai-workstation-platform" \
      org.opencontainers.image.upstream="https://github.com/aaif-goose/goose" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libssl3 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 goose \
    && useradd  --system --uid 1000 --gid goose --home-dir /home/goose --shell /bin/bash --create-home goose \
    && mkdir -p /workspace /home/goose/.config/goose \
    && chown -R goose:goose /workspace /home/goose

COPY --from=fetch --chown=root:root /tmp/goose /usr/local/bin/goose
COPY --chown=root:root entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=goose:goose config.yaml.example /home/goose/.config/goose/config.yaml.example
RUN chmod 0755 /usr/local/bin/goose /usr/local/bin/entrypoint.sh

ENV GOOSE_VERSION="${GOOSE_VERSION}" \
    GOOSE_PROVIDER="openai" \
    GOOSE_MODEL="qwen-coder-32b" \
    OPENAI_HOST="" \
    OPENAI_API_KEY="" \
    HOME="/home/goose" \
    XDG_CONFIG_HOME="/home/goose/.config"

USER goose
WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["session"]
