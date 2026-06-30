"""
共享 WebRTC 服务端逻辑 — handler 创建、路由注册、pipeline 启动。

两个入口文件（bot_js_client.py、server_prebuilt.py）各传各的回调，
避免重复的 transport 创建、WorkerRunner 等代码。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import BackgroundTasks, FastAPI
from loguru import logger

from pipecat.frames.frames import LLMRunFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from src.core.pipeline import build_pipeline

# ═══════════════════════════════════════════════════════════════
# 配置（可通过环境变量覆盖）
# ═══════════════════════════════════════════════════════════════

LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    "http://serverhome.tail2e6efb.ts.net/litellm/hermes",
)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "fuck_key")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

_llm_config = {"llm_base_url": LLM_BASE_URL, "llm_api_key": LLM_API_KEY, "llm_model": LLM_MODEL}


# ═══════════════════════════════════════════════════════════════
# Handler
# ═══════════════════════════════════════════════════════════════

def create_handler() -> SmallWebRTCRequestHandler:
    """创建共享的 WebRTC 连接管理器。"""
    return SmallWebRTCRequestHandler(
        ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
    )


# ═══════════════════════════════════════════════════════════════
# /start 端点
# ═══════════════════════════════════════════════════════════════

def register_start_endpoint(app: FastAPI, session_id: str = "default") -> None:
    """注册 POST /start 端点（JS 客户端协议要求）。"""
    @app.post("/start")
    async def start_bot():
        logger.info("/start received")
        return {
            "sessionId": session_id,
            "iceConfig": {
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
            },
        }


# ═══════════════════════════════════════════════════════════════
# SDP Offer + ICE Candidates 端点
# ═══════════════════════════════════════════════════════════════

def register_webrtc_endpoints(
    app: FastAPI,
    handler: SmallWebRTCRequestHandler,
    run_pipeline: Callable[[SmallWebRTCConnection], Awaitable[None]],
) -> None:
    """注册 SDP Offer 和 ICE Candidates 端点。

    Args:
        app: FastAPI 实例。
        handler: WebRTC 连接管理器。
        run_pipeline: 收到连接后启动 pipeline 的 async 函数。
    """
    @app.post("/sessions/{session_id}/api/offer")
    async def offer_session(
        session_id: str,
        request: SmallWebRTCRequest,
        background_tasks: BackgroundTasks,
    ):
        async def on_connection(connection: SmallWebRTCConnection) -> None:
            background_tasks.add_task(run_pipeline, connection)

        return await handler.handle_web_request(
            request=request,
            webrtc_connection_callback=on_connection,
        )

    @app.patch("/sessions/{session_id}/api/offer")
    async def ice_candidate_session(
        session_id: str,
        request: SmallWebRTCPatchRequest,
    ):
        return await handler.handle_patch_request(request)


# ═══════════════════════════════════════════════════════════════
# Pipeline 启动器工厂
# ═══════════════════════════════════════════════════════════════

def make_run_pipeline(
    *,
    on_client_ready: Callable[[Any, LLMContext, SmallWebRTCConnection], Awaitable[None]]
    | None = None,
    on_transport_created: Callable[[SmallWebRTCTransport, Any, SmallWebRTCConnection], Awaitable[None]]
    | None = None,
) -> Callable[[SmallWebRTCConnection], Awaitable[None]]:
    """创建 _run_pipeline 函数。

    Args:
        on_client_ready: (rtvi, context, connection) → 浏览器就绪时回调。
                          不传则默认注入 system prompt + 推开场白。
        on_transport_created: (transport) → transport 创建后的钩子。
                               bot_js_client.py 用此挂 _inject_inbound。
    """
    async def _run(connection: SmallWebRTCConnection):
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                video_in_enabled=False,
                video_out_enabled=False,
            ),
        )

        worker, context = build_pipeline(
            transport=transport,
            **_llm_config,
        )

        # ── 注册 client_ready 事件 ──
        if on_client_ready:
            # 传过来的回调自己负责注册事件处理器
            await on_client_ready(worker, context, connection)
        else:
            @worker.rtvi.event_handler("on_client_ready")
            async def _default_ready(rtvi):
                logger.info(f"[{connection.pc_id}] Client ready")
                context.add_message({
                    "role": "developer",
                    "content": "简短的给一点点有用的信息10-40个字以内。"
                })
                await worker.queue_frames([LLMRunFrame()])

        runner = WorkerRunner()
        await runner.add_workers(worker)
        logger.info(f"[{connection.pc_id}] pipeline starting")

        # ── Transport 创建后钩子 ──
        if on_transport_created:
            await on_transport_created(transport, worker, connection)

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

    return _run
