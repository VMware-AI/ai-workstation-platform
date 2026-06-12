# Packer 构建脚本 — 已归档（2026-05-28 团队决议）

> 这些脚本曾用于 Packer 自烘 Agent Platform Golden Image。**自 2026-05-28 决议起，平台不再自烘镜像**，改由客户运维 / admin 提供 vCenter 模板。

## 为什么下线

- **Scope pivot**：[`2026-05-20-scope-pivot.md`](../../../../docs/plans/2026-05-20-scope-pivot.md) 把底层基础设施划给客户运维提供。
- **设计决议**：[`docs/architecture/02-vm-lifecycle.md`](../../../../docs/architecture/02-vm-lifecycle.md) §2 "镜像准备期"明确改为 admin 准备模板。
- **维护成本**：自维护 OS + 工具链镜像 = 每次客户 OS 版本变动我们都要跟。让客户用他们的标准模板更顺。

## 保留这些文件的意义

留作历史快照 + 给 admin 准备模板时的**参考清单**（哪些 base hardening / 工具链是平台依赖的）。详细 admin checklist 见 [`packages/agent-platform-image/docs/template-requirements.md`](../../docs/template-requirements.md)。

## 脚本一览

| 脚本 | 历史用途 | 客户模板**应满足**的等价要求 |
|---|---|---|
| `00-wait-cloud-init.sh` | Packer 注入 OS 完成等待 | 模板内 cloud-init 已正确装好 |
| `01-base-hardening.sh` | SSH 硬化、netplan、清 machine-id / DHCP lease | 模板**必须**做这些（见 template-requirements） |
| `02-install-toolchain.sh` | docker、curl、jq 等工具 | 模板需预装 docker + 基础工具 |
| `03-install-agents.sh` | 预拉 goose 镜像 / 二进制兜底 | **可选**优化：预拉常用 agent docker image |
| `04-bootstrap-platform.sh` | 创建系统服务账户 `agent-platform` 等 | 此账户已**不再需要**（决策 1B：per-VM owner 账户在 cloud-init 时建） |
| `99-verify.sh` | Packer 烘前自检 | 与新设计无对应 |

## 不要做的事

- ❌ **不要**直接复活这些脚本跑 Packer build — CI 的 image-build job 已关
- ❌ **不要**修改它们 —— 修复请去 `template-requirements.md` 提需求
- ✅ **可以**作为离线参考材料给客户运维讲解平台为什么要这些 base 配置

---

下线日期：2026-05-29
关联决议：02-vm-lifecycle.md 决议 2 + 决议 2D + Packer 下线
