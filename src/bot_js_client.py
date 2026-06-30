#!/usr/bin/env python3
"""
Pipecat Voice Agent — 适配 JS 客户端的 SmallWebRTC 服务端。

架构：
  JS 客户端 (Vite SPA)
    ↓ POST /start, POST /sessions/{id}/api/offer, PATCH /sessions/{id}/api/offer
  SmallWebRTCRequestHandler (aiortc)
    ↓ WebRTC PeerConnection
  SmallWebRTCTransport → Pipeline → ...
    输入音频 → VAD → STT → user_agg → LLM → BotText → TTS → 输出音频

端点：
  POST /start                 分配 session，返回 ICE 配置
  POST /sessions/{id}/api/offer  接收 SDP Offer → 启动 pipeline
  PATCH /sessions/{id}/api/offer  接收 ICE candidates → 建立连接
  POST /inject_test_audio     注入测试音频（调试用，未来可删除）
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.core.webrtc_server import (
    create_handler,
    make_run_pipeline,
    register_start_endpoint,
    register_webrtc_endpoints,
)
from src.helpers.test_audio import test_audio

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")

logging.basicConfig(level=logging.WARNING)

# ── FastAPI app ──
app = FastAPI(title="Pipecat JS-Client Voice Agent")

# ── CORS：允许 JS 客户端跨端口/跨机器访问 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebRTC 连接管理器 ──
handler = create_handler()
test_audio.set_handler(handler)

# ── Pipeline 启动器（带测试音频注入钩子） ──

async def _setup_inject_inbound(transport, worker, conn):
    """transport 创建后，把 input() 挂到 connection 上供 inject 查找。"""
    conn._inject_inbound = transport.input()

run_pipeline = make_run_pipeline(
    on_transport_created=_setup_inject_inbound,
)

# ── 注册协议端点 ──
register_start_endpoint(app)
register_webrtc_endpoints(app, handler, run_pipeline)


# ═══════════════════════════════════════════════════════════════
# 根路径
# ═══════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
async def index(request: Request):
    """根路径。

    如果前端已 build（client/javascript/dist/ 存在），
    返回 index.html → 同端口服务。
    否则重定向到 Vite dev server（开发模式）。
    """
    dist_index = os.path.join(
        os.path.dirname(__file__), "../client/javascript/dist/index.html"
    )
    if os.path.isfile(dist_index):
        from fastapi.responses import FileResponse
        return FileResponse(dist_index)
    # 开发模式：重定向到 Vite dev server
    redirect_url = f"{request.url.scheme}://{request.url.hostname}:8764/"
    return RedirectResponse(url=redirect_url)


# ═══════════════════════════════════════════════════════════════
# 测试音频（调试/演示用，未来可整体删除）
# ═══════════════════════════════════════════════════════════════

@app.post("/inject_test_audio")
async def inject_test_audio_latest():
    """向最新 session 注入测试音频。JS 端 window.__injectTestAudio() 调用此端点。"""
    return await test_audio.inject_latest()


# ═══════════════════════════════════════════════════════════════
# 静态文件（生产模式）
# ═══════════════════════════════════════════════════════════════

_dist_dir = os.path.join(os.path.dirname(__file__), "../client/javascript/dist")
if os.path.isdir(_dist_dir):
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="client")
    logger.info(f"静态文件: {_dist_dir}")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    import uvicorn
    logger.info(f"Pipecat JS-Client Bot: http://{HOST}:{PORT}/")
    logger.info(f"LLM config: {handler}")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
