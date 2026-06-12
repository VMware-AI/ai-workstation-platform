# M0.8 NSX/vCenter Lab 摸底（10 题）

> 填写完毕后另存到 `docs/research/m0-nsx-lab-info.md` 入 git。
> 这 10 题必须全有答案才能跑 #23 起 NSX segment。

| # | 问题 | 答案 |
|---|---|---|
| 1 | vCenter URL（仅 host） | _eg. vcenter.lab.local_ |
| 2 | vCenter 版本 | _eg. 8.0u2_ |
| 3 | NSX Manager URL | _eg. nsx.lab.local_ |
| 4 | NSX-T 版本 | _eg. 4.1_ |
| 5 | Compute Cluster 名（创建 VM 用） | |
| 6 | Overlay Transport Zone 名 | |
| 7 | Tier-1 Gateway 名（PoC 共用 or 新建） | |
| 8 | Edge Cluster 名 + 规格 | |
| 9 | 模板 VM（已开 SSH，有 agent-platform-poc 私钥）名 | _eg. ubuntu-2204-template_ |
| 10 | 网段 / IP 池分配权限 | □ 我们自管 □ 必须找网管 |

## 必须提前申请

- [ ] vCenter 账号 `agent-platform-poc@vsphere.local`，权限 = VirtualMachine.* + Network.Assign
- [ ] NSX 账号 `agent-platform-poc`，role = Network Admin（或自定义最小）
- [ ] 至少 1 个新建 / 可用的 Tier-1 Gateway（不污染生产）
- [ ] 至少 3 台机器（或 3 容器）做 fake "员工 PC / FS / LLM"，分别在 .env 配 IP

## 阻塞性

如下 5 个无答案 → 0.8 PoC 完全无法启动：
- #1 + #3 URL
- #5 / #6 / #7
- vCenter + NSX 凭据
