# ⚠️ agent-plugins/ 已冻结退场（2026-06-10）

plugin 契约（`install-agent.sh` 调 `<kind>.sh` 的 `plugin_install/configure/start/healthcheck`）
**不再是 agent 安装的事实源**。单一事实源已收敛到 **C21 runcmd 注册表**：

- `packages/agent-platform-web-ui/src/lib/providers/vsphere/cloudinit/agents.ts`
- 自包含（curl 内网镜像 tar + sha256 门 + `su` 到用户），已真机验证通过、已离线化。

决策见 [doc 35](../../../docs/architecture/35-2026-06-09-c1-c21-agent-install-convergence.md)。

## 为什么暂时不删

C1 `VmwareProvisioner` 仍渲染 `cloud-init/user-data.yaml.tpl`，其 runcmd 调 `install-agent.sh`
（hands-on / pending 后端，有渲染测试）。删了会破 C1 测试。待 C1→C21 后端收敛立项时，
连同 `install-agent.sh` + 本目录 + `docs/plugin-interface.md` 一并清退。

**现在请勿在此新增或扩展 plugin。** 加新 agent → 改 `agents.ts` 注册表。
