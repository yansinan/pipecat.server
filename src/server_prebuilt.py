"""
Pipecat Voice Agent — 官方 PrebuiltUI (pipecat-ai-prebuilt) + SmallWebRTC.
端口 8766.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from loguru import logger
from pipecat.frames.frames import LLMRunFrame

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.workers.runner import WorkerRunner

from src.core.webrtc_server import (
    create_handler,
    make_run_pipeline,
    register_start_endpoint,
    register_webrtc_endpoints,
)

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8766"))
HOST = os.environ.get("HOST", "0.0.0.0")

# System prompt（用户可配）
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "你是一个友好的中文语音助手。回答简洁自然，适合口语对话。",
)

app = FastAPI(title="Pipecat PrebuiltUI Voice Agent")

handler = create_handler()

# ── Pipeline 启动器（带 pending 消息刷新） ──

async def _on_client_ready(worker, context, connection: SmallWebRTCConnection):
    """注册 client_ready 事件：注入 system prompt + 开场白。"""
    @worker.rtvi.event_handler("on_client_ready")
    async def ready(rtvi):
        logger.info(f"[{connection.pc_id}] Client ready via RTVI")
        context.add_message({"role": "system", "content": SYSTEM_PROMPT})
        await worker.queue_frames([LLMRunFrame()])

async def _post_transport(transport, worker, connection: SmallWebRTCConnection):
    """transport 创建后：刷新 pending app-messages（DTLS 握手时序问题）。"""
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        logger.info(f"[{connection.pc_id}] flushing {len(pending)} queued app-messages")
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()

run_pipeline = make_run_pipeline(
    on_client_ready=_on_client_ready,
    on_transport_created=_post_transport,
)

# ── 注册协议端点（共享 handler + run_pipeline） ──
register_start_endpoint(app, session_id="x")
register_webrtc_endpoints(app, handler, run_pipeline)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/client/")


from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI
app.mount("/client", PipecatPrebuiltUI, name="client")


def main() -> None:
    import uvicorn
    logger.info(f"Pipecat PrebuiltUI: http://{HOST}:{PORT}/")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
