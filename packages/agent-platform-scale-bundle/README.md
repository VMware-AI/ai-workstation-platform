# agent-platform-scale-bundle (C9)

Pack an Agent Platform release tree into a single signed artifact for offline customer install.

## 安装

```bash
uv sync                                  # workspace 内开发，提供 agent-platform-bundle
# 或单独装 CLI：uv tool install agent-platform-scale-bundle
```

需要 PATH 上有 `cosign` 二进制（见下方 Dependencies）。

## Public API

```python
from agent_platform_scale_bundle import pack, sign, verify

pack(Path("./release"), Path("dist/agent-platform-1.0.0.tar.zst"))
sign(Path("dist/agent-platform-1.0.0.tar.zst"), Path("cosign.key"),
     Path("dist/agent-platform-1.0.0.tar.zst.sig"), password="")
verify(Path("dist/agent-platform-1.0.0.tar.zst"), Path("cosign.pub"),
       Path("dist/agent-platform-1.0.0.tar.zst.sig"))
```

## CLI

```bash
agent-platform-bundle pack ./release dist/agent-platform-1.0.0.tar.zst
agent-platform-bundle sha256 dist/agent-platform-1.0.0.tar.zst          # 打印摘要
COSIGN_PASSWORD='' agent-platform-bundle sign dist/agent-platform-1.0.0.tar.zst --key cosign.key
agent-platform-bundle verify dist/agent-platform-1.0.0.tar.zst --key cosign.pub
# exit 0 = ok, 2 = pack error, 3 = sign error, 4 = verify mismatch
```

`agent-platform-installer` (C8) re-exports the `verify` subcommand — that is the only
path used at customer install time.

## Dependencies

- `click` (CLI)
- `zstandard` (only required for `pack()`; verify works without it)
- `cosign` binary on PATH (install: `brew install cosign` or [release](https://github.com/sigstore/cosign/releases))

## Tests

```bash
cd packages/agent-platform-scale-bundle
pytest                       # unit tests (no cosign needed, uses fake binary)
pytest -m integration        # end-to-end with real cosign (skipped if not installed)
```

## Design decisions

See `docs/research/scale-bundle-security.md` (delivered separately in PR #43)
for the cosign-vs-minisign-vs-GPG selection and
[`docs/runbooks/cosign-key-rotation.md`](../../docs/runbooks/cosign-key-rotation.md)
for the key rotation procedure.
