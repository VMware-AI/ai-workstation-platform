#!/bin/bash
set -e
set -o pipefail

# 全栈一键启动（#266）：仅需 Docker，不需要宿主机 Node 工具链。
#   ./start.sh        → compose 起 postgres/redis/migrate/web/worker（--wait 验健康）
#   ./start.sh dev    → 开发模式：基础设施进容器，web 在宿主机 npm run dev
#   ./start.sh seed   → 灌 demo 账号（可重复执行）
MODE="${1:-full}"
case "$MODE" in full|dev|seed) ;; *)
  echo "用法: ./start.sh [full|dev|seed]（默认 full）" >&2; exit 2 ;;
esac

echo "🚀 agent-platform-web-ui 启动中 (${MODE})..."

# .env 缺失则交互生成（纯 bash，不需要 Node）
if [ ! -f .env ]; then
  echo "未发现 .env，先生成密钥..."
  bash scripts/setup-env.sh
fi

# 构建指纹（#313）：web/worker 启动日志可断版本
export GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [ "$MODE" = "seed" ]; then
  # --build：代码更新后 seed 跑新镜像，不复用陈旧镜像
  docker compose --profile seed run --rm --build seed
  echo "✅ demo 账号已就绪"
  exit 0
fi

if [ "$MODE" = "dev" ]; then
  # 仅 dev 路径需要把 .env 读进 shell（pg_isready/npx 用）；compose 自己会读 .env
  set -a; source .env; set +a
  docker compose up -d postgres redis
  echo "检查数据库连接..."
  until pg_isready -h localhost -p 5432 -U "${POSTGRES_USER:-agentplatform}" 2>/dev/null; do
    echo "等待 PostgreSQL..."
    sleep 2
  done
  echo "运行数据库迁移..."
  npx prisma migrate deploy 2>&1 | tail -3
  npx prisma generate 2>&1 | tail -2
  echo "启动制备 worker（容器，崩溃自愈；--no-deps：迁移已在宿主机完成，跳过 migrate 镜像构建）..."
  docker compose up -d --no-deps --build provisioner-worker
  echo "启动 Next.js dev（端口 3000）..."
  npm run dev
  exit 0
fi

# 全栈：migrate 先行（web/worker depends_on 它成功）；--wait 阻塞到
# healthcheck 全绿，未达健康即非零退出——"启动成功"由 compose 证明而非打印。
if docker compose up -d --build --wait; then
  echo ""
  echo "✅ 全栈已启动：http://localhost:3000"
  echo "   docker compose ps          # 状态（migrate 应为 Exited(0)）"
  echo "   ./start.sh seed            # 灌 demo 账号（首次登录前跑一次）"
  echo "   docker compose logs -f web # 日志"
else
  echo ""
  echo "❌ 启动未达健康状态，排查：" >&2
  echo "   docker compose ps                # 哪个服务不健康" >&2
  echo "   docker compose logs migrate web  # 迁移/web 启动日志" >&2
  exit 1
fi
