# 客户 vCenter 模板准备 checklist

> 2026-05-29 团队决议后，平台不再自烘镜像。本文档是 admin / 客户运维准备 VM 模板的**全部要求**。模板做到下面 7 项就可被平台 clone。
>
> 关联设计：[`docs/architecture/02-vm-lifecycle.md`](../../../docs/architecture/02-vm-lifecycle.md) §2

---

## 1. OS 基线

| 项 | 推荐 | 必须 |
|---|---|---|
| 发行版 | Ubuntu 22.04 LTS（其他 LTS 也可，需平台测试支持） | linux x86_64 + systemd 已启用 |
| 内核 | HWE 或 GA | ≥ 5.15 |
| 文件系统 | ext4 或 xfs | 任意 systemd 支持的 |
| 时区 | UTC | 任意一致 |
| Locale | `en_US.UTF-8`（建议加 `zh_CN.UTF-8`） | UTF-8 默认 |

---

## 2. cloud-init（**关键**）

| 项 | 要求 |
|---|---|
| 已装 | `cloud-init` ≥ 22.x，含 `vmware` datasource 模块 |
| datasource | 配置成 **VMware GuestInfo 优先**：`/etc/cloud/cloud.cfg.d/99-vmware.cfg` 内容如下 |

```yaml
datasource_list: [ VMware, OVF, NoCloud ]
datasource:
  VMware:
    allow_raw_data: true
    vmware_cust_file_max_wait: 10
```

| 项 | 要求 |
|---|---|
| `users:` 模块启用 | 是（平台 cloud-init userdata 用它建 per-VM owner 账户） |
| `write_files:` 模块启用 | 是 |
| `runcmd:` 模块启用 | 是 |
| 完成后做 `cloud-init clean --logs` | 是（**避免 instance-id 冲突**） |

---

## 3. machine-id / 网络 / 唯一性清理

模板**不能**带 clone 残留：

| 项 | 命令 |
|---|---|
| 清空 machine-id | `truncate -s 0 /etc/machine-id` + `ln -sf /etc/machine-id /var/lib/dbus/machine-id` |
| 清 DHCP lease | `rm -f /var/lib/dhcp/dhclient*.leases` |
| 网卡名持久化关闭 | 删 `/etc/udev/rules.d/70-persistent-net.rules` 如存在 |
| netplan | `/etc/netplan/99-cloud-init.yaml` 配置 `dhcp4: true` on eth0（或客户标准命名） |
| 主机名 | 不在模板里固定（cloud-init guestinfo.metadata 写入） |

---

## 4. 必装工具链

```bash
apt-get install -y \
    docker.io          \  # agent docker mode
    curl jq            \  # cloud-init scripts
    openssh-server     \  # 用户 SSH 进 VM
    sudo               \  # plugin 用 sudo -u 切到 owner 身份
    cifs-utils         \  # 挂载 SMB（C19 vSAN Fileshare）
```

可选优化（预装常用 agent docker image，减少首次拉取时间）：
```bash
docker pull registry.customer.internal/agent-platform/goose:1.34.1
```

---

## 5. 安全硬化

| 项 | 要求 |
|---|---|
| SSH 密码登录 | `PasswordAuthentication no`（必须） |
| Root 登录 | `PermitRootLogin no`（必须） |
| 公钥登录 | `PubkeyAuthentication yes`（保留） |
| ufw / firewalld | 端口 22 必须开放；7681（ttyd）由 `install-agent.sh` 在 cloud-init 阶段自动 `ufw allow 7681/tcp`（W-3.2，无 ufw 时静默跳过） |
| ttyd 二进制 | 模板**无需预装**——`install-agent.sh` 优先 `apt-get install -y ttyd`（Ubuntu 22.04 universe），失败时回退抓 GitHub release prebuilt（W-3.2） |
| snapd / apparmor / whoopsie | 关掉（可选，但减少冷启动时间） |
| 审计 | journald 持久化 `/var/log/journal` |

---

## 6. 不需要 / 不应该在模板里做的事

| 不做 | 为什么 |
|---|---|
| ❌ 预装 Agent Platform 平台代码 | 平台代码靠 cloud-init 注入 |
| ❌ 创建任何业务用户账户 | 由 cloud-init `users:` 段动态建（决策 1B） |
| ❌ 写死 vCenter / NSX 凭据 | 凭据走 C18 token exchange |
| ❌ 装 `agent-platform-*` 系统账户 | 决策 1B 后改为 per-VM owner-named 账户 |
| ❌ 关闭 cloud-init 服务 | 平台 100% 依赖它 |

---

## 7. 验收测试（admin 把模板放进 vCenter 后用）

把模板 clone 一台 VM、不动 extraConfig、power-on。期望：

1. VM 起来后 `systemctl status cloud-init` 显示 active
2. `journalctl -u cloud-init` 没有 fatal
3. 模板内不存在的 instance-id 冲突报错（machine-id 清空成功标志）
4. `eth0` 拿到 DHCP IP
5. `id agent-platform` 命令应当 **fail**（不再有静态 `agent-platform` 系统账户）
6. SSH 端口可达（22）但密码登录被拒（key-only 验证）

通过这 6 条就可以注册进平台：

```bash
# admin 提交模板路径 + cosign 签名（决策 12）
curl -X POST https://agent-platform-control.customer.internal/admin/image-versions \
    -H "Authorization: Bearer ${admin_token}" \
    -d '{
      "version": "v0.1.0-customer",
      "template": "[Datastore01] templates/agent-platform-base-v0.1.0/agent-platform-base-v0.1.0.vmtx",
      "signature": "<cosign sig>"
    }'
# (PR-E 落地后启用)
```

---

## 8. 故障排查

| 现象 | 排查方向 |
|---|---|
| cloud-init 不跑 | datasource 配置错；`cat /run/cloud-init/result.json` 看具体原因 |
| `users:` 段没建账户 | cloud-init 版本太旧 / `users` 模块被禁 |
| install-agent.sh 报 `linux account '${owner}' missing` | 同上 — 检查 users 段 |
| guest hostname 没改 | VMware tools 没装 / datasource 没读到 guestinfo.metadata |
| docker 不可用 | `systemctl status docker`；模板里 docker.io 没装或被关掉 |

---

## 9. 与决策的对应

| 模板项 | 决议 |
|---|---|
| 不带业务用户 | 决议 1B（VM 内 owner-named 账户在 cloud-init 时建） |
| cloud-init `users:` 必须启用 | 决议 1B + 2D |
| VMware GuestInfo datasource | 决议 2（OVF extraConfig 注入） |
| 不带 secret | 决议 4 + 决议 9（secret 走 token exchange） |
| machine-id 清空 | 决议 2（唯一性） |
| 不自烘镜像（平台不做 Packer） | 2026-05-28 Packer 下线决议 |
