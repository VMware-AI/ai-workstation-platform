#!/usr/bin/env bash
#
# Interactive .env bootstrapper for agent-platform-web-ui.
#
# Copies .env.example -> .env (if missing) and fills the required secrets. For
# each secret you press Enter to auto-generate (openssl rand) or paste your own
# value. Idempotent: a secret that already has a non-placeholder value is left
# untouched, so re-running is safe.
#
# Usage:  npm run setup:env      (or)   bash scripts/setup-env.sh
#
# ENV_FILE / EXAMPLE may be overridden (used by tests); they default to the
# package's .env and .env.example.
set -euo pipefail

cd "$(dirname "$0")/.."  # package root

ENV_FILE="${ENV_FILE:-.env}"
EXAMPLE="${EXAMPLE:-.env.example}"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ ! -f "$EXAMPLE" ]]; then
    echo "✗ 找不到 $EXAMPLE，无法初始化 $ENV_FILE" >&2
    exit 1
  fi
  cp "$EXAMPLE" "$ENV_FILE"
  echo "✓ 已从 $EXAMPLE 创建 $ENV_FILE"
fi

# Current value of KEY in ENV_FILE, with any inline comment + surrounding
# whitespace stripped. Empty if the key is absent.
current() {
  { grep -E "^$1=" "$ENV_FILE" || true; } | head -1 | sed -E "s/^$1=//; s/#.*//; s/[[:space:]]*$//"
}

# Replace (or append) KEY=VALUE in ENV_FILE. Uses '|' as the sed delimiter so
# values containing '/' (e.g. DATABASE_URL) need no escaping; only '|' and '&'
# are escaped — neither appears in hex secrets or our URLs.
set_kv() {
  local key="$1" val="$2"
  local esc; esc=$(printf '%s' "$val" | sed -e 's/[|&]/\\&/g')
  if grep -qE "^$key=" "$ENV_FILE"; then
    sed -i.bak -E "s|^$key=.*|$key=$esc|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# A value counts as "unset" if it's empty or a known placeholder.
is_placeholder() {
  case "$1" in
    "" | CHANGE_ME*) return 0 ;;
    *) return 1 ;;
  esac
}

# Prompt for a secret. Enter -> auto-generate via `openssl rand -hex <bytes>`.
# Already-set secrets are skipped.
ensure_secret() {
  local key="$1" bytes="$2" desc="$3"
  local cur; cur=$(current "$key")
  if ! is_placeholder "$cur"; then
    echo "• $key 已设置，跳过"
    return
  fi
  local input=""
  read -r -p "  ${key}（${desc}）[回车=自动生成]: " input || true
  if [[ -z "$input" ]]; then
    input=$(openssl rand -hex "$bytes")
    echo "    ✓ 已自动生成"
  fi
  set_kv "$key" "$input"
}

echo "=== agent-platform-web-ui 环境初始化（${ENV_FILE}）==="
ensure_secret ENCRYPTION_KEY 32 "32 字节 AES 密钥，加密存储的 vCenter 密码 / apiKey"
ensure_secret POSTGRES_PASSWORD 24 "Postgres 密码"

# Wire DATABASE_URL to the (possibly freshly generated) POSTGRES_PASSWORD when it
# still carries the example placeholder.
db_url=$(current DATABASE_URL)
pg_pw=$(current POSTGRES_PASSWORD)
if [[ "$db_url" == *CHANGE_ME* && -n "$pg_pw" ]]; then
  set_kv DATABASE_URL "postgresql://$(current POSTGRES_USER):${pg_pw}@127.0.0.1:5432/$(current POSTGRES_DB)"
  echo "• DATABASE_URL 已用 POSTGRES_PASSWORD 填好"
fi

echo ""
echo "✓ ${ENV_FILE} 就绪。下一步："
echo "    docker compose up -d postgres redis"
echo "    npx prisma migrate deploy && npx prisma generate && npm run seed"
echo "    npm run dev          # 另开终端：npm run worker"
