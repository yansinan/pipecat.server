#!/usr/bin/env bash
# ============================================================================
# install.sh — 环境安装脚本（首次部署时执行一次）
# ============================================================================
# 功能：
#   1. 查找 uv 可执行文件
#   2. 创建 Python venv（uv sync）
#   3. 创建 Whisper / Silero 模型缓存目录
#   4. 安装前端依赖 + 构建生产包
#
# 用法：
#   bash install.sh
#
# 之后每次重启服务用 restart_webrtc_client.sh
# ============================================================================
set -euo pipefail

# ── 脚本所在目录（项目根目录） ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. 找 uv ──
# 尝试几个常见安装路径，找到第一个可用的
echo "→ 查找 uv..."
UV=""
for c in "$HOME/.hermes/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.local/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    [[ -x "$c" ]] && { UV="$c"; break; }
done
if [[ -z "$UV" ]]; then
    echo "错误：找不到 uv（Python 包管理器）"
    echo "请先安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "   uv: $UV"

# ── 2. 创建 venv（如果没有的话） ──
if [[ ! -d .venv ]]; then
    echo "→ 创建 Python 虚拟环境..."
    "$UV" sync --all-extras
else
    echo "→ 虚拟环境已存在，跳过"
fi

# ── 3. 创建模型缓存目录 ──
# Whisper 模型（~2GB）和 Silero VAD 模型下载到这里，避免散落在 home 目录
echo "→ 创建缓存目录..."
mkdir -p "$SCRIPT_DIR/cache/whisper" "$SCRIPT_DIR/cache/silero"
echo "   WHISPER_CACHE_DIR = $SCRIPT_DIR/cache/whisper"
echo "   SILERO_CACHE_DIR  = $SCRIPT_DIR/cache/silero"

# ── 4. 安装前端依赖 + Build ──
echo "→ 安装前端依赖..."
cd "$SCRIPT_DIR/client/javascript"
npm install --silent 2>/dev/null
echo "→ Build 前端..."
npm run build
cd "$SCRIPT_DIR"

echo ""
echo "✅ 安装完成！"
echo "   启动服务: bash restart_webrtc_client.sh"
