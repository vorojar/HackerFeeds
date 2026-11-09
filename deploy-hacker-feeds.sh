#!/bin/bash
# HackerFeeds 部署到小米 15 Pro（runit 服务 hub-app-hacker-feeds，端口 8905）。
# 只传 hacker-feeds.py + feeds.json；服务启动时读 feeds.json，更新数据后
# 须 sv restart（见脚本末尾），不像收盘图那样读目录即生效。
set -euo pipefail
PROXY="bash $HOME/workspace/mac-ssh/ts-proxy.sh %h %p"
PHONE="u0_a717@100.93.28.43"
COMMON_OPTS=(-o "ProxyCommand=$PROXY" -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/tmp/xiaomi15_known -o BatchMode=yes
  -o ConnectTimeout=20 -i "$HOME/.ssh/id_ed25519")
SSH_OPTS=("${COMMON_OPTS[@]}" -p 8022)
SCP_OPTS=("${COMMON_OPTS[@]}" -P 8022)
phone_ssh() { ssh "${SSH_OPTS[@]}" "$PHONE" "$@"; }
phone_scp() { scp "${SCP_OPTS[@]}" "$@"; }

cd "$(dirname "$0")"
[ -f hacker-feeds.py ] && [ -f feeds.json ] || { echo "missing hacker-feeds.py or feeds.json"; exit 1; }
phone_ssh 'mkdir -p ~/server/apps/hacker-feeds'
# 译文文件存在才一起传（首次部署前可能还没有）；服务启动时读它，缺失也不影响。
if [ -f translations_zh.json ]; then
  phone_scp hacker-feeds.py feeds.json translations_zh.json "$PHONE:server/apps/hacker-feeds/"
else
  phone_scp hacker-feeds.py feeds.json "$PHONE:server/apps/hacker-feeds/"
fi
phone_ssh 'mkdir -p /data/data/com.termux/files/usr/var/service/hub-app-hacker-feeds'
phone_ssh 'cat > /data/data/com.termux/files/usr/var/service/hub-app-hacker-feeds/run <<EOF
#!/data/data/com.termux/files/usr/bin/sh
umask 077
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export PATH=/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
export HOST=100.93.28.43
export PORT=8905
exec /data/data/com.termux/files/usr/bin/python -u /data/data/com.termux/files/home/server/apps/hacker-feeds/hacker-feeds.py 2>&1
EOF
chmod +x /data/data/com.termux/files/usr/var/service/hub-app-hacker-feeds/run'
sleep 3
# 已有服务在跑时只传文件不会生效（服务启动时才读 feeds.json 和代码），
# 所以这里总是重启一次；首次部署时 runsvdir 已自动拉起，restart 同样可用。
phone_ssh '/data/data/com.termux/files/usr/bin/sv restart /data/data/com.termux/files/usr/var/service/hub-app-hacker-feeds || true'
sleep 5
phone_ssh '/data/data/com.termux/files/usr/bin/sv status /data/data/com.termux/files/usr/var/service/hub-app-hacker-feeds; curl -s http://100.93.28.43:8905/healthz; echo'
echo "deploy done: http://100.93.28.43:8905/ (Tailscale 内网)"
