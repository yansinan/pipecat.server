#!/usr/bin/env bash
# ============================================================================
# restart_webrtc_client.sh — 重启 WebRTC 语音机器人服务
# ============================================================================
# 功能：
#   1. 读取 .env 环境变量
#   2. 停掉端口 8765 上的旧进程
#   3. 检查前端是否已 build（没有则自动 build）
#   4. 启动 bot_js_client（FastAPI + SmallWebRTC）
#
# 用法：
#   bash restart_webrtc_client.sh          # 重启（默认 :7860）
#   PORT=8080 bash restart_webrtc_client.sh # 指定端口
#
# 首次部署先执行 install.sh 安装环境
# ============================================================================
set -euo pipefail

# ── 脚本所在目录（项目根目录） ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 确保 venv 存在（安装过才会走通） ──
if [[ ! -d .venv ]]; then
    echo "错误：未找到 .venv，请先执行 bash install.sh"
    exit 1
fi

# ── 找 uv ──
UV=""
for c in "$HOME/.hermes/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.local/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
done
[[ -z "$UV" ]] && { echo "uv 未安装，请执行 bash install.sh"; exit 1; }

# ── 加载 .env ──
# .env 里可以配 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / PORT 等
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
    echo "→ 已加载 .env"
else
    echo "→ 未找到 .env，使用默认配置"
fi

# ── 停掉端口上的旧进程 ──
PORT="${PORT:-8765}"
PID="$(lsof -ti ":$PORT" 2>/dev/null || true)"
if [[ -n "$PID" ]]; then
    echo "→ 停旧进程 (PID $PID, 端口 $PORT)..."
    # 先温和 kill，最多等 10 秒
    kill "$PID" 2>/dev/null || true
    for i in {1..10}; do
        ! kill -0 "$PID" 2>/dev/null || sleep 1
    done
    # 10 秒还没死就强制 kill
    kill -9 "$PID" 2>/dev/null || true
    echo "   已停"
fi

# ── 检查前端是否已 build ──
DIST="$SCRIPT_DIR/client/javascript/dist"
if [[ ! -d "$DIST" ]]; then
    echo "→ 前端未 build，执行构建..."
    cd client/javascript
    npm install --silent 2>/dev/null
    npm run build
    cd "$SCRIPT_DIR"
else
    echo "→ 前端已 build，跳过"
fi

# ── 启动服务 ──
echo "→ 启动 bot_js_client :$PORT ..."
echo "   访问: http://localhost:$PORT/"
exec "$UV" run --project "$SCRIPT_DIR" python -m src.bot_js_client "$@"
