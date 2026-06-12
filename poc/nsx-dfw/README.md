# M0.8 NSX DFW PoC — 环境

> 关联 Issue：[#22](https://github.com/VMware-AI/ai-workstation-platform/issues/22) (本 PR)；后续 #23–#32
> 关联 plan：`docs/plans/m0/0.8-nsx-dfw-poc-plan.md`

## 目标

验证 per-tenant NSX Security Group + DFW 规则能：
1. 同租户 VM/PC/FS 互通
2. 跨租户硬隔离（含 ICMP）
3. VM → 0.0.0.0/0 拒绝（air-gap）
4. 规则变更 ≤ 30 秒生效
5. 控制面 API 自动维护，幂等

## 准备步骤

### 1) 摸底问卷（必填）

复制并填：
```bash
cp lab-infra-checklist.md ../../docs/research/m0-nsx-lab-info.md
# 填好后入 git
```

里面 10 个问题决定后续脚本能否跑（vCenter URL / NSX Manager / Edge Cluster / T1 / TZ / 已有 RP / 凭据获取方式）。

### 2) 申请专用账号

- **vCenter**：`agent-platform-poc@vsphere.local` 权限 = VirtualMachine.* + Network.Assign
- **NSX**：`agent-platform-poc` 权限 = Network Admin（或自定义最小：GET/POST 在 `/policy/api/v1/infra/domains/default/`）

让 NSX/vCenter 管理员发凭据邮件给 Wei。

### 3) `.env` 配置

```bash
cp .env.example .env
chmod 600 .env  # 含密码，别 commit
# 填 NSX_HOST / NSX_USER / NSX_PASSWORD / VCENTER_* / COMPUTE_CLUSTER / TRANSPORT_ZONE / T1_GATEWAY
```

### 4) 装依赖（开发本机）

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install requests python-dotenv pyVmomi pytest
```

### 5) 连通性 smoke test

```bash
python scripts/preflight.py
# 期望: NSX 200, vCenter 200, T1 exists, TZ exists 五行 OK
```

## 后续 Issue 怎么用本 PR 产物

| Issue | 用 | 备注 |
|---|---|---|
| #23 (0.8.2) Segments | `scripts/setup_segments.py` (本 PR) | 创 2 segment |
| #24 (0.8.3) VM | `scripts/provision_vms.py` (本 PR) | 起 4 fake VM |
| #25 (0.8.4) SG | `scripts/setup_groups.py` (本 PR) | dynamic group by tag |
| #26 (0.8.5) DFW rules | `templates/per-tenant-policy.json.j2` + `scripts/setup_dfw.py` | jinja2 模板 |
| #27 (0.8.6) positive tests | `scripts/test_positive.py` | pytest + paramiko |
| #28 (0.8.7) negative tests | `scripts/test_negative.py` | pytest + 必须 100% 拒绝 |
| #29 (0.8.8) latency | `scripts/test_rule_latency.py` | 规则切换 ≤ 30s |
| #30 (0.8.9) scale | `scripts/scale_estimate.py` | 文档/计算 |
| #31 (0.8.10) provision_tenant | `provision_tenant.py` | 独立 PR (#?) 实现幂等版 |
| #32 (0.8.11) report | 汇总到 `docs/research/m0-nsx-dfw-poc-report.md` | |

## 本 PR 交付（骨架）

- [x] `README.md` — 准备步骤 + 后续 issue 路径
- [x] `lab-infra-checklist.md` — 10 题 NSX/vCenter 摸底
- [x] `.env.example` — 11 个环境变量
- [x] `scripts/preflight.py` — 连通性 + 资源存在性 smoke
- [x] `scripts/_common.py` — NSX REST client 封装（其他脚本复用）
- [x] `scripts/setup_segments.py` — segment 创建（#23 用）
- [x] `scripts/setup_groups.py` — SG 创建（#25 用）
- [x] `templates/per-tenant-policy.json.j2` — DFW policy 模板（#26 用）
- [x] `tests/test_common.py` — _common.py 单元测试（mock requests）

## 不交付（属于后续 Issue 或独立 PR）

- ❌ 实际跑 setup_segments（要 NSX lab）
- ❌ VM 起（#24）/ positive/negative tests（#27/#28）
- ❌ provision_tenant.py 幂等版（#31 独立 PR）

## NSX-T 版本兼容矩阵

本 PR 脚本用 `/policy/api/v1` 路径，兼容 NSX-T 3.2+ / 4.x。

| API | 路径 | NSX-T 3.2 | 4.0 | 4.1 | 4.2 |
|---|---|:---:|:---:|:---:|:---:|
| Segments | `/infra/segments/{id}` | ✅ | ✅ | ✅ | ✅ |
| Groups (dynamic) | `/infra/domains/default/groups/{id}` | ✅ | ✅ | ✅ | ✅ |
| Security Policy | `/infra/domains/default/security-policies/{id}` | ✅ | ✅ | ✅ | ✅ |
| Services | `/infra/services/{id}` | ✅ | ✅ | ✅ | ✅ |
| Tags | VM tagging via vCenter + NSX inventory sync | ✅ | ✅ | ✅ | ✅ |

> 客户在 3.x 上跑出 API 字段变化，反馈到 Wei，加 version-aware switch。
