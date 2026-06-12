# Agent Plugin 接口规范

> ⚠️ **DEPRECATED（2026-06-10）**：plugin 契约已冻结退场。新 agent 走 C21 runcmd 注册表（`agent-platform-web-ui/.../cloudinit/agents.ts`），不要照本写新 plugin。详见 [doc 35](../../../docs/architecture/35-2026-06-09-c1-c21-agent-install-convergence.md)。


> Cloud-init `install-agent.sh` 与各 agent 实现之间的契约。每个 agent 是 `agent-plugins/${AGENT_KIND}.sh` 一个文件，暴露四个 shell 函数。
>
> 关联设计：[`docs/architecture/02-vm-lifecycle.md`](../../../docs/architecture/02-vm-lifecycle.md) §决策 2D。

---

## 1. 文件位置与发现

```
镜像内：
  /opt/agent-platform/cloud-init/install-agent.sh         ← 调度器（所有 plugin 共用）
  /opt/agent-platform/agent-plugins/<kind>.sh             ← 每个 agent 一个文件
  /opt/agent-platform/agent-plugins/<kind>.sh.placeholder ← 未启用的 agent（冻结 / 待发布）
```

`install-agent.sh` 通过 `source "/opt/agent-platform/agent-plugins/${AGENT_KIND}.sh"` 动态加载。`AGENT_KIND` 从 `/etc/agent-platform/install.env`（由 cloud-init userdata 占位符 `{{ AGENT_KIND }}` 渲染）读入。

**不存在的 plugin**：`install-agent.sh` 检测到 `${AGENT_KIND}.sh` 不存在或为 `.placeholder` 时，**立即 fail 而非降级**，并打 educational error 到 systemd journal。

---

## 2. Plugin 必须暴露的 4 个函数

每个函数接收 0 参数（环境变量传参）；返回 0 = 成功，非 0 = 失败。

### 2.1 `plugin_install`

```bash
plugin_install() {
    # 必做：把 agent 二进制 / docker image / venv 装到本机。
    # 幂等：重复调用必须安全。
    # 超时建议：≤ 5 分钟（受 ${REGISTRY_URL} 网络影响）。
}
```

### 2.2 `plugin_configure`

```bash
plugin_configure() {
    # 必做：写 agent 自己的配置文件 / 环境文件。
    # 不做：启动服务（那是 plugin_start）。
    # 输入：所有 ${AGENT_PLATFORM_*} env vars。
    # 输出：${AGENT_RUNTIME_ENV} 路径下的 env 文件（约定 /etc/agent-platform/<kind>.env，0600）。
    # 注意：plugin 写完后，install-agent.sh 会把该文件 chown 到 ${AGENT_USER}
    # —— agent 是 systemd --user 服务，--user 管理器以用户身份读 EnvironmentFile，
    # root:root 0600 会读不了（#280）。plugin 不必自己 chown，但若改写该文件要保持
    # ${AGENT_USER} 可读。
}
```

### 2.3 `plugin_start`

```bash
plugin_start() {
    # 必做：把 agent 启动为 systemd --user service（agent 跑在 ${AGENT_USER} 身份下）。
    # 单元路径：/home/${AGENT_USER}/.config/systemd/user/agent.service
    # 已 enable-linger，重启后自动起。
    # 启动后等 healthcheck 一次（≤ 30s），失败返非 0。
}
```

### 2.4 `plugin_healthcheck`

```bash
plugin_healthcheck() {
    # 必做：探活 + 验出口（agent 能连 LLM gateway）。
    # 用于 install-agent.sh 完成后的"自检 → token exchange"边界。
    # 超时建议：≤ 10s；首启需跑 DB 迁移的 agent（如 xiaoguai serve 的 sqlx
    # migrate）可放宽到 ≤ 60s，但须在 plugin 注释里说明原因。
    # 返 0 = ready；返非 0 = 详细错误打 stderr（被 install-agent.sh 转译为教学性 error）。
}
```

---

## 3. Plugin 看到的环境变量（input contract）

`install-agent.sh` 在 source plugin 前 `set -a` 导出这些：

| 变量 | 来源（cloud-init 占位符 → install.env） | 用途 |
|---|---|---|
| `AGENT_KIND` | `{{ AGENT_KIND }}` | 用于自定位（plugin 自己一般不读，调度器读） |
| `AGENT_VERSION` | `{{ AGENT_VERSION }}` | docker tag / tarball 版本 |
| `AGENT_USER` | `{{ AGENT_USER }}` | linux 账户名（决策 1B） |
| `AGENT_USER_UID` | `{{ AGENT_USER_UID }}` | UID（用于 chown） |
| `REGISTRY_URL` | `{{ REGISTRY_URL }}` | docker / 二进制 mirror |
| `GATEWAY_URL` | `{{ LITELLM_GATEWAY_URL }}` | LLM gateway 出口 |
| `GATEWAY_API_KEY` | （由 token exchange 写入 `/etc/agent-platform/runtime.env`，**plugin_configure 前不可读**）| LLM bearer |
| `HEARTBEAT_URL` | `{{ HEARTBEAT_URL }}` | 心跳上报 |
| `AGENT_PLATFORM_USER_TOKEN` | `{{ AGENT_PLATFORM_USER_TOKEN }}` | bootstrap token（仅用于 token exchange，**不持久化到 plugin 自己的 env**） |
| `AGENT_RUNTIME_ENV` | 常量 `/etc/agent-platform/${AGENT_KIND}.env` | plugin_configure 输出路径 |

---

## 4. 调度器调用顺序

```
install-agent.sh:
  1. 验 install.env 存在 + 必填字段
  2. 验 AGENT_USER 已被 cloud-init users: 段建好
  3. source /opt/agent-platform/agent-plugins/${AGENT_KIND}.sh
  4. plugin_install                       │ 失败 → fail + journal
  5. POST /api/cloud-init/exchange-token  │ 取 secrets，写 /etc/agent-platform/runtime.env
  6. plugin_configure                     │
  7. loginctl enable-linger ${AGENT_USER} │ 让 user systemd 服务在用户未登录时也跑
  8. plugin_start                         │
  9. plugin_healthcheck                   │ 失败 → fail + journal
  10. systemctl restart agent-platform-heartbeat │ 通知控制面 ready
```

第 5 步是控制面状态机推进到 `ready` 的关键钩子（详 [`02-vm-lifecycle.md`](../../../docs/architecture/02-vm-lifecycle.md) §决策 4 / 1.11.3）。

---

## 5. Plugin 不允许做的事

| 禁 | 为什么 |
|---|---|
| 在 plugin_* 函数里读 stdin / 提示交互 | cloud-init 非交互 |
| 启动除 agent 之外的系统级 service | 越权 |
| 修改 `/etc/agent-platform/install.env`（只读） | 调度器拥有 |
| 写 `/home/${AGENT_USER}/.ssh/`（由 token exchange 写） | 凭据面边界 |
| 持有 `AGENT_PLATFORM_USER_TOKEN` 在文件里 | bootstrap token 仅用于 exchange，单次性 |
| 绕过 ${GATEWAY_URL} 直连外网 LLM | 跨凭据面 + 跳过计费 |
| 修改 systemd 系统单元（`/etc/systemd/system/`） | agent 跑 `--user`，不在系统级 |

---

## 6. 最小可用 plugin 模板

```bash
#!/usr/bin/env bash
# /opt/agent-platform/agent-plugins/example.sh
# 示例：1-binary, 1-systemd-unit agent

set -euo pipefail

plugin_install() {
    local image="${REGISTRY_URL}/example-agent:${AGENT_VERSION}"
    docker pull "${image}"
}

plugin_configure() {
    install -m 0600 -o root -g root /dev/null "${AGENT_RUNTIME_ENV}"
    cat > "${AGENT_RUNTIME_ENV}" <<EOF
OPENAI_BASE_URL=${GATEWAY_URL}
OPENAI_API_KEY=${GATEWAY_API_KEY}
HEARTBEAT_URL=${HEARTBEAT_URL}
EOF
}

plugin_start() {
    local unit_dir="/home/${AGENT_USER}/.config/systemd/user"
    sudo -u "${AGENT_USER}" mkdir -p "${unit_dir}"
    sudo -u "${AGENT_USER}" tee "${unit_dir}/agent.service" >/dev/null <<EOF
[Unit]
Description=Example Agent

[Service]
EnvironmentFile=${AGENT_RUNTIME_ENV}
ExecStart=/usr/bin/docker run --rm --env-file ${AGENT_RUNTIME_ENV} ${REGISTRY_URL}/example-agent:${AGENT_VERSION}
Restart=on-failure

[Install]
WantedBy=default.target
EOF
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="/run/user/${AGENT_USER_UID}" \
        systemctl --user daemon-reload
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="/run/user/${AGENT_USER_UID}" \
        systemctl --user enable --now agent.service
}

plugin_healthcheck() {
    sudo -u "${AGENT_USER}" XDG_RUNTIME_DIR="/run/user/${AGENT_USER_UID}" \
        systemctl --user is-active agent.service >/dev/null
}
```

---

## 7. 参考实现

| Plugin | 状态 |
|---|---|
| `agent-plugins/xiaoguai.sh` | 🟢 默认首选 agent；pip 装 + `xiaoguai serve` daemon（#277） |
| `agent-plugins/goose.sh` | 🟢 由原 `install-goose.sh` 移植 |
| `agent-plugins/qoder.sh.placeholder` | ⛔ 冻结，待阿里 BD 回函（[`docs/architecture/07-open-questions.md`](../../../docs/architecture/07-open-questions.md) OQ-2） |

---

## 8. 增加新 agent 的开发清单

1. 复制本文 §6 模板 → `agent-plugins/<kind>.sh`
2. 实现 4 个函数；保持幂等
3. 跑 `tests/test_agent_plugin_<kind>.bats`（写一份 bats 测试，覆盖 install / configure / healthcheck 路径）
4. 在 [`docs/architecture/03-components/c20-agent-adapter.md`](../../../docs/architecture/03-components/c20-agent-adapter.md) 里加 adapter 行
5. 在 [`docs/architecture/06-roadmap.md`](../../../docs/architecture/06-roadmap.md) 状态矩阵更新
6. 在 `vm_package_specs.yaml` 里把 `<kind>` 加进 `allowed_agent_kinds`
