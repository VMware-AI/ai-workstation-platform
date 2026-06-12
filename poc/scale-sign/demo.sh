#!/usr/bin/env bash
# M0.4 scale-bundle 签名最小 PoC
# 强成功标准: 正常包 verify PASS, 篡改 1 字节后 verify FAIL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "${1:-}" = "--clean" ]; then
  rm -f demo.key demo.pub dummy.tar.zst dummy.tar.zst.sig README.txt
  echo "cleaned."
  exit 0
fi

command -v cosign >/dev/null 2>&1 || { echo "cosign not installed — see README.md" >&2; exit 2; }
command -v zstd >/dev/null 2>&1 || { echo "zstd not installed — brew install zstd" >&2; exit 2; }

echo "[1/6] Generate ephemeral keypair..."
rm -f demo.key demo.pub
# COSIGN_PASSWORD env makes generation non-interactive
COSIGN_PASSWORD="demo-pw" cosign generate-key-pair --output-key-prefix demo >/dev/null

echo "[2/6] Build dummy.tar.zst (containing one README.txt)"
echo "this is a dummy scale bundle, version 0.0.0" > README.txt
tar --create --use-compress-program=zstd --file=dummy.tar.zst README.txt
ORIG_SHA=$(shasum -a 256 dummy.tar.zst | awk '{print $1}')
echo "    sha256: $ORIG_SHA"

echo "[3/6] cosign sign-blob -> dummy.tar.zst.sig"
COSIGN_PASSWORD="demo-pw" cosign sign-blob \
  --key demo.key \
  --output-signature dummy.tar.zst.sig \
  --yes \
  dummy.tar.zst >/dev/null

echo "[4/6] cosign verify-blob (正常包)"
if cosign verify-blob --key demo.pub --signature dummy.tar.zst.sig dummy.tar.zst 2>/dev/null; then
  echo "    PASS: signature verified"
else
  echo "    UNEXPECTED FAIL on clean bundle" >&2
  exit 1
fi

echo "[5/6] Tamper 1 byte in dummy.tar.zst"
# 写一个完全不同的字节到末尾
printf '\x99' >> dummy.tar.zst
NEW_SHA=$(shasum -a 256 dummy.tar.zst | awk '{print $1}')
echo "    sha256 (tampered): $NEW_SHA"

echo "[6/6] cosign verify-blob (篡改后)"
if cosign verify-blob --key demo.pub --signature dummy.tar.zst.sig dummy.tar.zst 2>/dev/null; then
  echo "    UNEXPECTED PASS on tampered bundle — PoC FAILED" >&2
  exit 1
else
  echo "    FAIL: signature verification failed (expected)"
fi

echo
echo "DONE — demo passed."
