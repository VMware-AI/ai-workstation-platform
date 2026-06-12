# Agent Platform Web UI — 安装与测试指南（给新同事）

从零把 web-ui 跑起来，并对一个真实 vCenter 做端到端部署测试（doc 33 原生 vSphere provisioning）。

> 设计与阶段背景见 [doc 33](../../docs/architecture/33-2026-06-02-web-ui-native-vsphere-provisioning.md) 与
> [P2-e Runbook](../../docs/status/2026-06-03-web-ui-p2c-complete-and-p2e-runbook.md)。本文只讲"怎么装、怎么测"。

---

## 1. 前置要求

| 需要 | 说明 |
|---|---|
| **Node.js ≥ 20** | Next.js 16；用 nvm 装 20 或 22 都行 |
| **npm** | 本包用 npm + `package-lock.json`（**不是** pnpm） |
| **Docker** | 起 Postgres + Redis 最省事（也可本机自己装） |
| **govc** | **仅真机测试需要**——跑制备的那台机器要能调 govc。https://github.com/vmware/govmomi/releases |
| **一个可连的 vCenter** | **仅真机测试需要**；自签证书也行 |

---

## 2. 一次性安装

```bash
# 1) 拉代码
git clone <repo-url> && cd ai-workstation-platform/packages/agent-platform-web-ui

# 2) 装依赖
npm install

# 3) 配置环境变量（交互式：回车=自动生成密钥，或粘贴已有值）
npm run setup:env
#   从 .env.example 建 .env，并填好这几个必填密钥（已设置的不会覆盖，可重复跑）：
#     ENCRYPTION_KEY        加密存储的 vCenter 密码 / apiKey；缺失会让"保存计算池"返回 500
#     INTERNAL_API_SECRET   控制面与 agent 的共享密钥
#     POSTGRES_PASSWORD     并据此自动补全 DATABASE_URL 的密码
#   其余按需手动编辑 .env：
#     NEXTAUTH_URL=http://localhost:3000
#     跨机访问浏览器再加： DEV_ALLOWED_ORIGINS=<你访问用的IP>   # 逗号分隔、不带 http://

# 4) 起 Postgres + Redis（用本包自带的 compose）
docker compose up -d postgres redis

# 5) 建表 + 生成 Prisma client + 种子账号
npx prisma migrate deploy
npx prisma generate
npm run seed            # 创建 demo 账号：demo@local.test / demo123456
```

> ⚠️ `.env` 里 `POSTGRES_PASSWORD` 和 `DATABASE_URL` 里的密码必须一致，否则连不上库。

---

## 3. 启动

制备是**异步**的：部署页只建实例 + 入队，真正调 govc 的是 **worker**。前端和 worker 是两个独立进程，**worker 不起，部署永远停在 PENDING**。

**前端**（前台一个终端）：

```bash
npm run dev            # http://localhost:3000 （也绑 0.0.0.0 → 局域网可访问）
```

**worker —— 二选一**（要真正 clone VM 才需要；纯看界面可不起）：

| 方式 | 命令 | 保活 | govc | 适合 |
|---|---|---|---|---|
| **A. 容器（推荐）** | `docker compose up -d --build provisioner-worker` | ✅ `restart: unless-stopped` 崩溃自愈 | 镜像内置 | demo / 长期挂机 |
| **B. 宿主机进程** | `npm run worker`（单独终端） | ❌ 关终端即停 | 需自己装 govc 进 PATH | 改 worker 代码、临时调试 |

> 方式 A 的 worker 在 compose 网络内，用**服务名**连 Postgres/Redis（compose 已自动注入，不读 .env 的 127.0.0.1）。日志：`docker compose logs -f provisioner-worker`。改了 worker 代码要 `--build` 重新打镜像。
>
> 方式 B 的 worker 是宿主机进程，读 .env 的 `127.0.0.1` 正好连到 compose 映射出的端口。关终端就没了——长期挂机请用方式 A，或用 systemd/tmux 守护。

打开 http://localhost:3000，用 `demo@local.test` / `demo123456` 登录。

**只看界面**：起 `npm run dev` 就够（Postgres 要在跑）。Redis/worker/govc 只有要真正 clone VM 时才需要。

> `./start.sh` 一把起齐 Postgres + Redis + worker（容器）+ dev（前台），是方式 A 的封装。

---

## 4. 真机测试（vSphere 端到端）

> 前置：govc 已装（`govc version` 能跑）、Redis 在跑、`npm run worker` 在跑、有可连的 vCenter。

1. **计算池**（侧边栏「计算池」）：新建 vSphere 池，填 host / username / password /（可选 datacenter）。自签证书勾「关闭 TLS 校验」。点 **「测试 VC」**应返回 ok（走 REST 探活）。
2. 同页点 **「浏览资源」**：应列出 datastore / 网络 / 资源池 / 文件夹 / **VM 模板**（走 govc）。**这一步第一次验证 govc 真能连上 vCenter。**
3. **实例页 →「从模板创建实例」**（`/instances/deploy`）：
   - 选计算池 → **「加载资源」** → 下拉填充。
   - **单台**：选 VM 模板、填主机名 / 网络(dhcp 或 static) / 系统用户 / 密码 / sshKey / 时区 / agent 类型。
   - **CSV 批量**：粘贴/上传 `vm_name,ip,netmask,gateway,dns,user,password,ssh_key`，看预览表 + 逐行报错。
   - 提交 → 建实例 + 入队。
4. **看 worker 终端日志** + 实例状态（实例页 5 秒轮询）：`PENDING → PROVISIONING → RUNNING`（或 `ERROR` 带可读原因）。
5. **进 VM 验证**：SSH 上去 → `cloud-init status` 应为 `done`，检查主机名 / 用户 / 网络 / agent 是否按表单生效。

详细成功标准 + govc 编排要盯的风险点见 [P2-e Runbook §3](../../docs/status/2026-06-03-web-ui-p2c-complete-and-p2e-runbook.md)。

---

## 5. 排障（都是真实踩过的）

| 现象 | 原因 / 解决 |
|---|---|
| 登录返回 **403** | CSRF 防护（比对 Origin host 与 Host header）。浏览器正常登录没问题；**跨机访问**要在 `.env` 设 `DEV_ALLOWED_ORIGINS=<访问IP>` 再重启 dev。用 curl 测要带 `-H "Origin: http://localhost:3000"`。 |
| 页面 500 / Prisma 报列不存在 | DB 没应用最新迁移。跑 `npx prisma migrate deploy && npx prisma generate`，**重启 dev 和 worker**。改 schema 后必须重新 generate + 重启。 |
| 实例一直 **PENDING** 不动 | worker 没起，或 Redis 没连上。确认 `npm run worker` 在跑、`docker compose ps` 里 redis 是 up、`REDIS_URL` 正确。看队列积压：`docker exec <redis容器> redis-cli llen bull:provision:wait`（>0 = 入队成功没人消费）。 |
| 登录**循环**（提交后又回登录页，跨机访问时） | Next.js 16 默认禁止跨 origin 拉 dev 资源，登录页 JS 被 403、表单逻辑没加载。`.env` 设 `DEV_ALLOWED_ORIGINS=<访问IP>` 再重启 dev。**不要**自己 cp 一个 `next.config.js` 进来——它会旁路 `next.config.ts`（安全头等全丢）。 |
| worker 报 **`Instance ... not found`** | web 和 worker 连了**不同的 Postgres**。两个常见成因：① 旧目录起的 dev server 还活着（带旧 env）——`ps aux \| grep next` 看进程目录，全杀掉同一目录重启；② `DATABASE_URL` 同时出现在 `.env.local` 和 `.env`——Next 读 `.env.local` 覆盖，worker 的 dotenv **只读 `.env`**。统一到 `.env` 一处。 |
| VM 起来了但 **agent 没装上 / 怀疑 runcmd（pip、curl）没跑通** | 进 VM 看 `cloud-init status --long` + `sudo cat /var/log/cloud-init.log`（runcmd 的网络命令报错都在这，常见是首启无 DNS / 出站被 NSX 挡）。想看部署时**实际生成**的 cloud-init：worker 端设 `CLOUDINIT_DEBUG_DIR=/some/dir`（**临时排错用，含密钥，用完删**）会把每台 VM 的 `*.userdata.yaml`/`*.metadata.yaml` 落盘；或从 VM 反掏：`govc object.collect <vm> config.extraConfig` 取 `guestinfo.userdata` → `base64 -d \| gunzip`。 |
| 「浏览资源 / 加载资源」报 **govc 未安装** | 跑 worker/dev 的机器装 govc 二进制并加进 PATH。 |
| 「浏览资源」报认证/证书失败 | vCenter 凭据错，或自签证书没关 TLS 校验（计算池里勾「关闭 TLS 校验」）。 |
| 启动告警：多 lockfile / middleware→proxy 弃用 | **无害**，可忽略。 |
| `npm run seed` 报错 | 确认 DB 已 `migrate deploy`、`DATABASE_URL` 对、`prisma.config.ts` 存在（Prisma 7 从这里读 seed 配置）。 |

### 手动用 govc 复现一台（排 agent 安装失败）

基础 cloud-init（用户/密码/网络）通了但 **agent 没装上**，多半是 runcmd 的联网命令（`pip install` / `curl`）在首启失败。用 `CLOUDINIT_DEBUG_DIR` 落盘的两个 yaml 手动复现，逐步定位：

```bash
# 1) 落盘实际生成的 cloud-init（worker 端）
export CLOUDINIT_DEBUG_DIR=/tmp/ci && npm run worker      # 然后部署一台
ls /tmp/ci/<vm>.userdata.yaml /tmp/ci/<vm>.metadata.yaml

# 2) 先肉眼核 userdata 的 runcmd 段：pip install xiaoguai / goose 那几行在不在、对不对

# 3) 手动注入同样的两个文件到一台克隆好的 powered-off VM（编码必须 gzip+base64）
govc vm.change -vm <vm> \
  -e guestinfo.userdata=$(gzip -c /tmp/ci/<vm>.userdata.yaml | base64 -w0) \
  -e guestinfo.userdata.encoding=gzip+base64 \
  -e guestinfo.metadata=$(gzip -c /tmp/ci/<vm>.metadata.yaml | base64 -w0) \
  -e guestinfo.metadata.encoding=gzip+base64
govc vm.power -on <vm>

# 4) 进 VM 看 runcmd 到底跑没跑通（这步才是定位根因的关键）
ssh <user>@<vmip>
cloud-init status --long
sudo grep -A3 -iE 'pip|xiaoguai|goose|curl|network|resolve' /var/log/cloud-init-output.log
# 手动重跑那条命令验证联网：python3 -m pip install xiaoguai==1.13.0
#   连不上 PyPI → 网络/DNS/NSX 出站问题（换部署路径也修不了，要放行出站或配内网 PyPI）
#   能连上但报错 → runcmd 命令本身的 bug
```

> 注意：govc 注入用的是 **gzip+base64**（见 `src/lib/providers/vsphere/govc.ts`），不是裸 base64——手动复现时编码方式要一致，否则 cloud-init 读不出来。

---

## 6. 升级（已经在跑的老用户）

代码更新后，**不要只 `git pull`**——依赖、数据库 schema、worker 镜像都可能变了。按这三步：

```bash
git pull
npm install            # 依赖可能更新了（package-lock 变化时必须）
./start.sh             # 重新跑迁移 + 重建 worker 镜像 + 重启 dev（:3000）
```

`./start.sh` 已经把升级要做的事都包了：`prisma migrate deploy`（应用新迁移）、`docker compose up -d --build provisioner-worker`（重建 worker 容器拿到新代码）、重启 dev。所以**老用户升级 = `git pull` + `npm install` + `./start.sh`**。

> - 如果你的 worker 是**宿主机进程**（`npm run worker`，不是容器），`./start.sh` 不会重启它——手动停掉旧的重新 `npm run worker`。
> - 升级后实例还停在 PENDING：多半是旧 worker 没换成新代码。确认 worker 容器已 `--build` 重建（`docker compose ps` 看 worker 的创建时间）。
> - 看到迁移失败：检查 `.env` 的 `DATABASE_URL`/`POSTGRES_PASSWORD` 是否一致（见排障表）。

---

## 7. 常用命令速查

```bash
docker compose up -d postgres redis     # 起依赖服务
docker compose ps                       # 看 postgres/redis 状态
npx prisma migrate deploy               # 应用迁移
npx prisma generate                     # 重新生成 client（改 schema 后）
npm run seed                            # 种子 demo 账号
npm run dev                             # 前端 + API（:3000）
npm run worker                          # 制备 worker（调 govc）
npm test                                # 单元测试（vitest）
npm run build                           # 生产构建（验证用）
```

## 更新代码后（重要）

worker 的超时等常量在**构建时**打进 bundle/镜像，拉新代码后必须重建并重启：

- 容器方式：`docker compose up -d --build provisioner-worker`（或直接 `./start.sh`）
- 直跑方式：`pkill -f "worker/index.ts"` 后重新 `npm run worker`（tsx 不热加载）

验证跑的是哪版：worker 启动第一行日志 `[worker] clone budget=30min git=<sha> built=<ts>`，
sha 对不上 `git rev-parse --short HEAD` 就是旧构建。


## 全栈一键启动（#266，仅需 Docker）

```bash
./start.sh        # postgres + redis + migrate(一次性) + web + worker 全容器
./start.sh seed   # 灌 demo 账号（首次登录前跑一次）
open http://localhost:3000
```

- `docker compose ps`：migrate 应为 `Exited (0)`，web/worker `healthy/running`
- web 容器内置 govc（计算池"浏览资源"与部署在容器内直连 vCenter）
- 数据落 volume（`docker compose down && up` 后仍在；迁移幂等）
- **更新代码后**：`./start.sh` 自带 `--build`；web/worker 启动日志第一行均带 git 指纹可断版本
- 开发模式（web 热重载在宿主机）：`./start.sh dev`
