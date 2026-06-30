#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 找 uv ──
UV=""
for c in "$HOME/.hermes/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.local/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
done
[[ -z "$UV" ]] && { echo "uv not found"; exit 1; }

# ── 确保 venv ──
[[ ! -d .venv ]] && "$UV" sync --all-extras

# ── 缓存目录 ──
export WHISPER_CACHE_DIR="$SCRIPT_DIR/cache/whisper"
export SILERO_CACHE_DIR="$SCRIPT_DIR/cache/silero"
export XDG_CACHE_HOME="$SCRIPT_DIR/cache"
mkdir -p "$WHISPER_CACHE_DIR" "$SILERO_CACHE_DIR"

# ── 加载 .env ──
[[ -f .env ]] && set -a && source .env && set +a

# ── 停老进程 ──
PORT="${PORT:-7860}"
PID="$(lsof -ti ":$PORT" 2>/dev/null || true)"
if [[ -n "$PID" ]]; then
    echo "→ 停旧进程 (PID $PID, 端口 $PORT)..."
    kill "$PID" 2>/dev/null || true
    for i in {1..10}; do
        ! kill -0 "$PID" 2>/dev/null || sleep 1
    done
    kill -9 "$PID" 2>/dev/null || true
fi

# ── Build 前端（有 dist 则跳过） ──
DIST="$SCRIPT_DIR/client/javascript/dist"
if [[ -d "$DIST" ]]; then
    echo "→ 前端已 build，跳过"
else
    echo "→ Build 前端..."
    cd client/javascript
    npm install --silent 2>/dev/null
    npm run build
    cd "$SCRIPT_DIR"
fi

# ── 启动 ──
echo "→ 启动 bot_js_client :$PORT ..."
exec "$UV" run --project "$SCRIPT_DIR" python -m src.bot_js_client "$@"
