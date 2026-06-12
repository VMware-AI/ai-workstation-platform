# scale-sign PoC

最小可重现 demo：cosign sign-blob → 验签 PASS → 篡改 → 验签 FAIL。

## 前置

```bash
# 装 cosign（任一即可）
brew install cosign            # macOS
# 或
go install github.com/sigstore/cosign/v2/cmd/cosign@latest
```

## 跑

```bash
./demo.sh
```

## 期望输出

```
[1/6] Generate ephemeral keypair...
[2/6] Build dummy.tar.zst (containing one README.txt)
[3/6] cosign sign-blob -> dummy.tar.zst.sig
[4/6] cosign verify-blob (正常包)
PASS: signature verified
[5/6] Tamper 1 byte in dummy.tar.zst
[6/6] cosign verify-blob (篡改后)
FAIL: signature verification failed (expected)

DONE — demo passed.
```

如最后一句不是 "DONE — demo passed."，则 PoC 失败，回 Issue #35 评论标注。

## 清理

```bash
./demo.sh --clean
```
