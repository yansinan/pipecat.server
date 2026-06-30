#!/bin/bash
# 重启 Pipecat PrebuiltUI Server (端口 8766)
# 用法: bash restart_prebuilt.sh
# Ctrl+C 干净退出

cd /home/dr/workspace/pipecat

# 杀掉旧进程(用 killall,只匹配 server_prebuilt 进程,不误杀其他 python 服务)
killall -9 -r "python.*server_prebuilt" 2>/dev/null
sleep 2
# 兜底:按端口再杀一次
for pid in $(ss -tlnp 2>/dev/null | grep 8766 | grep -oP 'pid=\K\d+'); do
    kill -9 "$pid" 2>/dev/null
done
sleep 1

# 打印代码时间戳
echo "=== 代码时间戳 ==="
stat --format="%y  %n" src/server_prebuilt.py src/core/pipeline.py src/services/edge_tts.py src/services/whisper_stt.py

# 启动
export PATH="/home/dr/.hermes/bin:$PATH"
echo "=== 启动服务器: http://localhost:8766/ (Ctrl+C 退出) ==="

# 前台启动，trap 把 SIGINT/SIGTERM 转发给 child
uv run --project . python -m src.server_prebuilt &
SERVER_PID=$!
trap "echo '[停止] killing PID '$SERVER_PID'...'; kill -INT $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; exit 0" INT TERM
wait $SERVER_PID
