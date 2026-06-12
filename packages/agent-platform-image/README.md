# agent-platform-image (C3)

Golden-image **cloud-init payload** for agent VMs: the cloud-init template the
control plane (C1) renders at clone time, a generic agent-install dispatcher, and
the per-agent plugins it runs inside the VM. Plus the spec customer ops follow to
prepare the vCenter template.

> **不再自烘镜像。** 平台自 2026-05-28 决议起停止用 Packer 自动构建黄金镜像
> （见 [`archive/packer/README.md`](archive/packer/README.md) 的下线说明），改由
> 客户运维 / admin 按 [`docs/template-requirements.md`](docs/template-requirements.md)
> 准备 vCenter 模板。本包现在只交付 **cloud-init payload**（wheel 仅含 `cloud-init/`），
> 由 C1 在 clone VM 时渲染注入。历史 Packer 脚本归档在 `archive/packer/`，仅作离线参考。

## 这个包提供什么

| 内容 | 路径 | 说明 |
|---|---|---|
| cloud-init 模板 | `cloud-init/user-data.yaml.tpl` | C1 clone 时渲染（用户、网络、bootstrap token、agent 类型） |
| agent 安装调度器 | `cloud-init/scripts/install-agent.sh` | 通用、非 agent 特定；按 `AGENT_KIND` source 对应 plugin |
| agent plugins | `agent-plugins/<kind>.sh` | 实现 `plugin_install/configure/start/healthcheck`。首选 **xiaoguai**（pip + `serve` daemon）、其次 **goose**，均已实现 |
| systemd units | `cloud-init/systemd/` | 心跳 timer + agent service |
| 客户模板规范 | `docs/template-requirements.md` | admin 准备 vCenter 模板的 checklist（标准） |
| plugin 契约 | `docs/plugin-interface.md` | 写新 agent plugin 的接口规范 |

## 用法

这个包不"安装运行"——它是被 C1 消费的 payload。常见操作：

```bash
cd packages/agent-platform-image

# 1) 验证 cloud-init 模板渲染（开发/CI）
python -m pytest tests/

# 2) 打 wheel 供 C1 消费（只含 cloud-init/）
python -m build           # → dist/agent_platform_image-*.whl
```

**准备 vCenter 模板（客户运维）**：照 [`docs/template-requirements.md`](docs/template-requirements.md)
做（Ubuntu 22.04 + cloud-init + open-vm-tools 等），模板就绪后 C1 从中 clone VM 并注入上面的
cloud-init payload。

**加一个新 agent 类型**：照 [`docs/plugin-interface.md`](docs/plugin-interface.md) 在
`agent-plugins/` 写 `<kind>.sh`（实现 4 个 `plugin_*` 函数）。

## VM 内启动流程（由 C1 注入后在 VM 内发生）

```
C1 clone VM → 注入渲染好的 user-data
  └─ cloud-init 运行 install-agent.sh
       ├─ 读 /etc/agent-platform/install.env（AGENT_KIND 等）
       ├─ source agent-plugins/${AGENT_KIND}.sh
       └─ plugin_install → plugin_configure → plugin_start → plugin_healthcheck
```

## 结构

```
agent-platform-image/
├── cloud-init/              # ← wheel 内容（payload）
│   ├── user-data.yaml.tpl   #   C1 渲染的模板
│   ├── scripts/             #   install-agent.sh（调度器）+ goose/heartbeat 脚本
│   └── systemd/             #   heartbeat timer + agent service
├── agent-plugins/           # goose.sh + *.sh.placeholder
├── docs/                    # template-requirements.md / plugin-interface.md
├── agents/                  # agent 容器镜像构建（goose）
├── tests/                   # cloud-init 渲染测试
└── archive/packer/          # 历史 Packer 脚本（已下线，勿复活，见其 README）
```

## 常见问题

**cloud-init 不执行？** 确认模板按 `docs/template-requirements.md` 装了 cloud-init +
open-vm-tools，且 C1 部署时 OVF/guestinfo 注入成功；VM 内查 `cloud-init status` 与
`/var/log/cloud-init.log`。

**想改镜像里预装什么？** 不要改 `archive/packer/` 脚本（已下线）——改
`docs/template-requirements.md` 提需求，由客户运维更新模板。

**Goose 之外的 agent？** 看 `agent-plugins/` 下的 placeholder + `docs/plugin-interface.md`。
