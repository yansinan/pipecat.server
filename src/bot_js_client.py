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

import asyncio
import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from loguru import logger

from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from src.core.pipeline import build_pipeline
from src.helpers.test_audio import test_audio

# ── 环境 ──
load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "7860"))
HOST = os.environ.get("HOST", "0.0.0.0")

# ── LLM 配置（默认指向自建 LiteLLM/Headroom 代理） ──
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    "http://serverhome.tail2e6efb.ts.net/litellm/hermes",
)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "fuck_key")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

logging.basicConfig(level=logging.WARNING)

# ── FastAPI app ──
app = FastAPI(title="Pipecat JS-Client Voice Agent")

# ── WebRTC 连接管理器 ──
webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)

# 把连接池引用注入 test_audio，供 inject 端点查找 session
test_audio.set_handler(webrtc_handler)

# ── CORS：允许 JS 客户端跨端口/跨机器访问 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 测试音频注入：inbound 挂在 connection._inject_inbound 上，通过 _pcs_map 查找 ──


# ═══════════════════════════════════════════════════════════════
# WebRTC 会话管理
# ═══════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
async def index(request: Request):
    """根路径 → 重定向到同主机的 Vite dev server（端口 5173）。"""
    redirect_url = f"{request.url.scheme}://{request.url.hostname}:5173/"
    return RedirectResponse(url=redirect_url)


@app.post("/start")
async def start_bot():
    """JS 客户端连接入口：分配 session，返回 ICE Server 配置。

    客户端收到 response 后用 sessionId + iceConfig 创建 PeerConnection。
    """
    session_id = str(uuid.uuid4())
    logger.info(f"[{session_id}] /start received")
    return {
        "sessionId": session_id,
        "iceConfig": {
            "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
        },
    }


async def _run_pipeline(connection: SmallWebRTCConnection):
    """在后台运行完整 pipeline。

    流程：
    1. 创建 SmallWebRTCTransport（绑定到 WebRTC 连接）
    2. 构建 pipeline（STT → LLM → TTS）
    3. 注册 client_ready 事件 → 推开场白
    4. 通过 WorkerRunner 运行，直到连接关闭
    """
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,   # 浏览器麦克风 → 服务端
            audio_out_enabled=True,  # 服务端 TTS → 浏览器
            video_in_enabled=False,  # 暂不处理视频
            video_out_enabled=False,
        ),
    )

    worker, context = build_pipeline(
        transport=transport,
        llm_base_url=LLM_BASE_URL,
        llm_api_key=LLM_API_KEY,
        llm_model=LLM_MODEL,
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        """浏览器客户端就绪 → 发开场白。"""
        logger.info(f"[{connection.pc_id}] Client ready — kicking off conversation")
        context.add_message(
            {"role": "developer", "content": "简短的给一点点有用的信息10-40个字以内。"}
        )
        from pipecat.frames.frames import LLMRunFrame
        await worker.queue_frames([LLMRunFrame()])

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{connection.pc_id}] pipeline starting")

    # 挂载 input transport 到 connection，供测试音频注入使用
    # 可以用 这两种方法直接推入音频切片：
    # await inbound._audio_in_queue.put(chunk)  # 推入独立队列
    # await inbound.push_frame(InputAudioRawFrame(data=chunk))  # 推入 pipeline
    connection._inject_inbound = transport.input()

    try:
        await runner.run(auto_end=False)
    except asyncio.CancelledError:
        logger.info(f"[{connection.pc_id}] pipeline cancelled")
        raise
    except Exception as e:
        logger.exception(f"[{connection.pc_id}] pipeline crashed: {e}")
        raise
    finally:
        logger.info(f"[{connection.pc_id}] pipeline done")


async def _handle_offer(
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    # ------------------------------------------------------------------
    # 接收 JS 端发来的 SDP Offer，创建 WebRTC 连接，然后启动 pipeline
    #
    # request          → JS 端 POST 过来的 {sdp, type, pc_id, ...}
    # background_tasks → FastAPI 后台任务（HTTP 响应后执行 pipeline）
    # 流程：
    #   1. 用 webrtc_handler.handle_web_request() 创建 PeerConnection
    #   2. PeerConnection 建好 → 回调 on_connection → 后台跑 pipeline
    # ------------------------------------------------------------------

    # 框架创建 PeerConnection，绑定回调：连接就绪后跑 pipeline
    async def on_connection(connection: SmallWebRTCConnection) -> None:
        background_tasks.add_task(_run_pipeline, connection)

    # 调框架：解析 SDP Offer → 创建 PeerConnection → 生成 Answer
    answer = await webrtc_handler.handle_web_request(
        request=request,
        webrtc_connection_callback=on_connection,
    )
    return answer


# ═══════════════════════════════════════════════════════════════
# 端点注册
# ═══════════════════════════════════════════════════════════════

# ---- SDP Offer ----
# SDP = Session Description Protocol。
# JS 客户端 startBot() 返回后，通过 POST 发送 SDP Offer。
# 服务端创建 PeerConnection，绑定 on_connection 回调启动 pipeline。
# session_id 来自 POST /start 的返回值。

@app.post("/sessions/{session_id}/api/offer")
async def offer_session(
    session_id: str,
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    return await _handle_offer(request, background_tasks)


# ---- ICE Candidates ----
# ICE = Interactive Connectivity Establishment。
# 浏览器推自己的 IP 地址列表，服务端登记到 aiortc 挑一个连。
# 同 SDP Offer，session_id 来自 POST /start。

@app.patch("/sessions/{session_id}/api/offer")
async def ice_candidate_session(session_id: str, request: SmallWebRTCPatchRequest):
    """接收 ICE candidates → 调用框架方法登记到 PeerConnection。"""
    return await webrtc_handler.handle_patch_request(request)


# ═══════════════════════════════════════════════════════════════
# 测试音频（调试/演示用，未来可整体删除）
# ═══════════════════════════════════════════════════════════════

# 如需彻底移除，删 src/helpers/test_audio.py 和下面这个端点即可。


@app.post("/inject_test_audio")
async def inject_test_audio_latest():
    """向最新 session 注入测试音频。JS 端 window.__injectTestAudio() 调用此端点。"""
    return await test_audio.inject_latest()


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════


def main():
    import uvicorn

    logger.info(f"Pipecat JS-Client Bot: http://{HOST}:{PORT}/")
    logger.info(f"LLM: {LLM_BASE_URL} model={LLM_MODEL}")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
