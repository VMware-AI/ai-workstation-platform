# M0.7 文件桥 PoC — vSAN File Services + SMB

> Task 0.7.1 / Issue #64 环境准备记录。目标是在真实 vSphere / vSAN / AD 环境开测前，把资质、IP Pool、账号权限、三端客户端和证据清单一次性确认清楚。

## 范围

本目录用于验证 vSAN File Services（SMB）+ Active Directory 的三端共享访问方案：

```
[vSAN 分布式存储]
    -> vSAN File Services (FSVM)
        -> SMB 共享 (per-user)
            -> macOS / Windows 10 / Ubuntu 22.04 Agent VM
                -> AD 认证 + per-user quota + 快照恢复
```

本任务只交付 PoC 环境准备，不启用 File Services，不创建 SMB share，也不填写未实际验证的环境值。后续任务：

| Task | Issue | 交付 |
|---:|---|---|
| 2 | #65 | 启用 vSAN File Services + AD Join |
| 3 | #66 | 创建 alice / bob / carol per-user SMB 共享 |
| 4-12 | #67-#75 | 三端 mount、i18n、大文件、quota、快照、AD、隔离测试 |
| 13 | #76 | 汇总 PoC 报告和团队签字 |

## 当前记录

| 项目 | 值 |
|---|---|
| PoC 状态 | `PREPARED_FOR_ENV_INPUT` |
| 记录日期 | 2026-05-19 |
| Repo commit | `b9ace6a` |
| PoC 分支 | `feat/biaotang-0.7.1-fileshare-vsan-env` |
| 计划文档 | `docs/plans/m0/0.7-fileshare-poc-plan.md` |
| 最终报告 | `docs/research/m0-fileshare-poc-report.md` |

## vSphere / vSAN 资质记录

| 检查项 | 期望 | 实际值 | 证据位置 | 状态 |
|---|---|---|---|:---:|
| vCenter 版本 | vSphere 8.0 U3+ | TBD | TBD | [ ] |
| ESXi 主机版本 | 8.0 U3+ preferred | TBD | TBD | [ ] |
| vSAN 类型 | ESA preferred; OSA acceptable if FS supported | TBD | TBD | [ ] |
| vSAN 集群主机数 | >= 3 | TBD | TBD | [ ] |
| vSAN datastore | 可见且健康 | TBD | TBD | [ ] |
| vSAN health | 无 critical alarm | TBD | TBD | [ ] |
| File Services 支持 | UI 可启用 SMB File Services | TBD | TBD | [ ] |
| DNS/NTP | vCenter, ESXi, AD, clients 时间和解析正常 | TBD | TBD | [ ] |

建议证据：

- vCenter `About` 截图或版本输出。
- vSAN Cluster `Monitor -> vSAN -> Skyline Health` 截图。
- vSAN datastore 截图。
- File Services enable wizard 前置检查截图。

## Active Directory 记录

| 检查项 | 期望 | 实际值 | 证据位置 | 状态 |
|---|---|---|---|:---:|
| AD Domain | 例如 `agent-platform.local` | TBD | TBD | [ ] |
| Domain Controller | IP/FQDN 可达 | TBD | TBD | [ ] |
| DNS Server | 能解析 AD 和 File Services 名称 | TBD | TBD | [ ] |
| File Services OU | 允许创建 FSVM / computer account | TBD | TBD | [ ] |
| Join 账号 | 有 AD join 权限，不记录密码 | TBD | TBD | [ ] |
| 测试用户 alice | 已创建，可登录 | TBD | TBD | [ ] |
| 测试用户 bob | 已创建，可登录 | TBD | TBD | [ ] |
| 测试用户 carol | 已创建，可登录 | TBD | TBD | [ ] |

禁止把 AD 密码、join 账号密码、SMB 用户密码写入 git。只记录账号名、权限范围和证据截图路径。

## File Services IP Pool 规划

vSAN File Services 需要为 FSVM 准备 IP Pool。PoC 期间先手动填写，再由 #65 使用。

| 项目 | 值 |
|---|---|
| File Services FQDN | TBD |
| Primary VIP / SMB endpoint | TBD |
| DNS suffix | TBD |
| VLAN / Portgroup | TBD |
| Subnet CIDR | TBD |
| Gateway | TBD |
| DNS servers | TBD |
| IP 数量 | TBD，应与 vSAN 集群主机数匹配 |

| FSVM | Planned IP | Host affinity / note | 状态 |
|---|---|---|:---:|
| fsvm-01 | TBD | TBD | [ ] |
| fsvm-02 | TBD | TBD | [ ] |
| fsvm-03 | TBD | TBD | [ ] |

预期 SMB 路径：

| 用户 | Share 名称 | SMB 路径 | Quota |
|---|---|---|---:|
| alice | `agent-platform-alice` | `\\<file-services-fqdn>\agent-platform-alice` | 50GB |
| bob | `agent-platform-bob` | `\\<file-services-fqdn>\agent-platform-bob` | 50GB |
| carol | `agent-platform-carol` | `\\<file-services-fqdn>\agent-platform-carol` | 50GB |

## 三端客户端准备

| 客户端 | 期望 | 实际值 | 必备工具 | 状态 |
|---|---|---|---|:---:|
| macOS | Sonoma 14+ | TBD | Finder, `mount_smbfs`, `dd`, screenshot tool | [ ] |
| Windows 10 | 干净测试 VM，可访问 AD/SMB | TBD | Explorer, PowerShell, `net use` | [ ] |
| Ubuntu 22.04 Agent VM | 可访问 AD/SMB | TBD | `cifs-utils`, `smbclient`, `dd`, `time` | [ ] |

网络连通性预检：

| 源 | 目标 | 协议/端口 | 期望 | 状态 |
|---|---|---|---|:---:|
| macOS | File Services FQDN | TCP/445 | reachable | [ ] |
| Windows 10 | File Services FQDN | TCP/445 | reachable | [ ] |
| Ubuntu 22.04 | File Services FQDN | TCP/445 | reachable | [ ] |
| vCenter / FSVM | AD DC | DNS, LDAP/Kerberos, SMB as needed | reachable | [ ] |

## Task 1 验收 Gate

Issue #64 可以标记完成的最低条件：

- [ ] vCenter / ESXi / vSAN 版本和健康状态已记录。
- [ ] vSAN 集群主机数和磁盘/ESA/OSA 模式已记录。
- [ ] AD Domain、DC、DNS、join 账号权限已确认。
- [ ] File Services IP Pool 已规划，IP 数量与 vSAN 主机数匹配。
- [ ] macOS / Windows 10 / Ubuntu 22.04 三端客户端已准备并记录 IP/版本。
- [ ] 后续 #65 可直接使用本 README 的环境值执行 File Services enable。

如果任一项无法确认，不要继续 #65；先在 PR 或 Issue #64 留下 blocker 说明。

## 产出文档索引

| 文件 | 对应任务 | 状态 |
|---|---|:---:|
| `README.md` | #64 Task 1 environment readiness | 当前文件 |
| `setup-fs.sh` | #65 Task 2 enable File Services | 待创建 |
| `create-shares.ps1` | #66 Task 3 create SMB shares | 待创建 |
| `results-macos.md` | #67 Task 4 macOS mount | 待创建 |
| `results-windows.md` | #68 Task 5 Windows mount | 待创建 |
| `results-ubuntu.md` | #69 Task 6 Ubuntu mount | 待创建 |
| `results-i18n.md` | #70 Task 7 i18n path | 待创建 |
| `results-perf.md` | #71 Task 8 large-file perf | 待创建 |
| `results-quota.md` | #72 Task 9 quota | 待创建 |
| `results-snapshot.md` | #73 Task 10 snapshot restore | 待创建 |
| `results-ad.md` | #74 Task 11 AD auth | 待创建 |
| `results-isolation.md` | #75 Task 12 isolation | 待创建 |

## 参考

- M0.7 PoC plan: `docs/plans/m0/0.7-fileshare-poc-plan.md`
- M0 readiness board: `https://github.com/VMware-AI/ai-workstation-platform/issues/46`
- Issue #64: `https://github.com/VMware-AI/ai-workstation-platform/issues/64`
