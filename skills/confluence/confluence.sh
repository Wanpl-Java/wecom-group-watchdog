#!/usr/bin/env bash
# Confluence 私有化部署 CLI helper（Bearer PAT 认证）
# 依赖：curl。所有请求走 https，认证头 Authorization: Bearer <token>
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=confluence.env
source "$SCRIPT_DIR/confluence.env"

BASE="${CONFLUENCE_BASE:-https://wiki.fit2cloud.cn}"
AUTH_HEADER="Authorization: Bearer ${CONFLUENCE_TOKEN}"
JSON_HDR="Accept: application/json"

case "${1:-help}" in
  spaces)
    curl -s -m 30 -H "$AUTH_HEADER" -H "$JSON_HDR" \
      "$BASE/rest/api/space?limit=${2:-25}"
    ;;
  search)
    curl -s -m 30 -G -H "$AUTH_HEADER" -H "$JSON_HDR" \
      "$BASE/rest/api/content/search" \
      --data-urlencode "cql=${2:?usage: search \"<cql>\"}" \
      --data-urlencode "limit=${3:-25}" \
      --data-urlencode "expand=space,body.view"
    ;;
  get)
    curl -s -m 30 -H "$AUTH_HEADER" -H "$JSON_HDR" \
      "$BASE/rest/api/content/${2:?usage: get <contentId>}?expand=body.view,space,version"
    ;;
  tree)
    curl -s -m 30 -G -H "$AUTH_HEADER" -H "$JSON_HDR" \
      "$BASE/rest/api/content" \
      --data-urlencode "spaceKey=${2:?usage: tree <spaceKey>}" \
      --data-urlencode "limit=${3:-25}" \
      --data-urlencode "expand=space"
    ;;
  page)
    curl -s -m 30 -G -H "$AUTH_HEADER" -H "$JSON_HDR" \
      "$BASE/rest/api/content/search" \
      --data-urlencode "cql=space=${2:?usage: page <spaceKey> <title>} and title=\"${3:?title required}\"" \
      --data-urlencode "limit=10" \
      --data-urlencode "expand=space,body.view"
    ;;
  *)
    cat <<'EOF'
Confluence 私有化 CLI（Bearer PAT 认证）
Usage:
  confluence.sh spaces [limit]             列出空间
  confluence.sh search "<cql>" [limit]     CQL 搜索内容
  confluence.sh get <contentId>            获取页面正文(storage)与元信息
  confluence.sh tree <spaceKey> [limit]    列出某空间下的页面
  confluence.sh page <spaceKey> <title>    按空间+标题定位页面
EOF
    ;;
esac
