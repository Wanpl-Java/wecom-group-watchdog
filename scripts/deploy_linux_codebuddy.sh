#!/usr/bin/env bash
# Linux 直连部署：Watchdog :8092 + CodeBuddy CLI --serve（不用 Nginx 反代）
# 用法（在目标机 root 下）：
#   bash scripts/deploy_linux_codebuddy.sh
# 可选环境变量：
#   APP_DIR=/opt/wecom-group-watchdog
#   WORKSPACE=/root/jumpserver_all
#   PUBLIC_BASE_URL=http://10.1.12.80:8092
#   CODEBUDDY_PORT=7890
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wecom-group-watchdog}"
WORKSPACE="${WORKSPACE:-/root/jumpserver_all}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://10.1.12.80:8092}"
CODEBUDDY_PORT="${CODEBUDDY_PORT:-7890}"
REPO_URL="${REPO_URL:-https://github.com/Wanpl-Java/wecom-group-watchdog.git}"

export PATH="${HOME}/.local/bin:${PATH}"

echo "==> 停用 Nginx 18092 反代（若存在，不删配置只停服务占用）"
if systemctl is-active --quiet nginx 2>/dev/null; then
  if [[ -f /etc/nginx/sites-enabled/wecom-watchdog-proxy.conf ]] \
    || [[ -f /etc/nginx/conf.d/wecom-watchdog-proxy.conf ]] \
    || grep -Rqs "18092" /etc/nginx 2>/dev/null; then
    echo "    检测到 18092/反代相关配置，停止 nginx（Watchdog 将直连 8092）"
    systemctl stop nginx || true
  fi
fi

echo "==> 安装/确认 CodeBuddy CLI"
if ! command -v codebuddy >/dev/null 2>&1; then
  curl -fsSL https://www.codebuddy.cn/cli/install.sh | bash
  export PATH="${HOME}/.local/bin:${PATH}"
fi
codebuddy --version

echo "==> 同步仓库到 ${APP_DIR}"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch --depth 1 origin main
  git -C "${APP_DIR}" reset --hard origin/main
else
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone --depth 1 "${REPO_URL}" "${APP_DIR}"
fi
cd "${APP_DIR}"

echo "==> Python venv + 依赖"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  echo "ERROR: ${APP_DIR}/.env 不存在。请先把本机 .env 拷到该路径再跑本脚本。"
  exit 1
fi

echo "==> 改写 .env 为 Linux 直连 + CodeBuddy"
python3 - <<'PY'
from pathlib import Path
import os
import re

p = Path(".env")
text = p.read_text(encoding="utf-8")
repl = {
    "PUBLIC_BASE_URL": os.environ.get("PUBLIC_BASE_URL", "http://10.1.12.80:8092"),
    "WORKBUDDY_MODE": "desktop",
    "WORKBUDDY_DESKTOP_URL": f"http://127.0.0.1:{os.environ.get('CODEBUDDY_PORT', '7890')}",
    "WORKBUDDY_DESKTOP_AGENT": "pulse",
    "WORKBUDDY_WORKSPACE": os.environ.get("WORKSPACE", "/root/jumpserver_all"),
    "WORKBUDDY_STYLE_SKILL": str(Path("workbuddy/jumpserver-support-style/SKILL.md").resolve()),
    "APP_HOST": "0.0.0.0",
    "APP_PORT": "8092",
    "WATCHDOG_INGEST_URL": "http://127.0.0.1:8092/ingest/messages",
}
for k, v in repl.items():
    pat = re.compile(rf"^{re.escape(k)}=.*$", re.M)
    line = f"{k}={v}"
    if pat.search(text):
        text = pat.sub(line, text)
    else:
        text = text.rstrip() + "\n" + line + "\n"
p.write_text(text, encoding="utf-8")
print("updated:", ", ".join(repl))
PY

mkdir -p data logs
if [[ ! -d "${WORKSPACE}" ]]; then
  echo "WARN: WORKSPACE 不存在: ${WORKSPACE}（建议放到 /root/jumpserver_all）"
fi

echo "==> systemd: codebuddy-serve"
cat >/etc/systemd/system/codebuddy-serve.service <<EOF
[Unit]
Description=CodeBuddy CLI serve for wecom-group-watchdog
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${WORKSPACE}
Environment=PATH=${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$(command -v codebuddy) --serve --host 127.0.0.1 --port ${CODEBUDDY_PORT} --auth none --agent pulse
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "==> systemd: wecom-group-watchdog"
cat >/etc/systemd/system/wecom-group-watchdog.service <<EOF
[Unit]
Description=WeCom Group Watchdog
After=network.target codebuddy-serve.service
Wants=codebuddy-serve.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
Environment=PATH=${APP_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8092
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now codebuddy-serve.service
systemctl enable --now wecom-group-watchdog.service

sleep 2
echo "==> 健康检查"
ss -lntp | grep -E "8092|${CODEBUDDY_PORT}" || true
curl -fsS "http://127.0.0.1:8092/healthz" && echo
curl -fsS -o /dev/null -w "codebuddy_http=%{http_code}\n" "http://127.0.0.1:${CODEBUDDY_PORT}/" || true
echo "DONE. PUBLIC_BASE_URL=${PUBLIC_BASE_URL}"
echo "外网/同事访问: ${PUBLIC_BASE_URL}  （直连 8092，无需 Nginx）"
