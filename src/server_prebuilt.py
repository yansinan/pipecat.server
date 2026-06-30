"""
Pipecat Voice Agent — 官方 PrebuiltUI (pipecat-ai-prebuilt) + SmallWebRTC.
端口 8766.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from loguru import logger

from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from src.core.pipeline import build_pipeline

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8766"))
HOST = os.environ.get("HOST", "0.0.0.0")

# LLM 配置：默认指向自建 LiteLLM/Headroom
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    "http://serverhome.tail2e6efb.ts.net/litellm/hermes",
)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "fuck_key")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

logging.basicConfig(level=logging.WARNING)

app = FastAPI(title="Pipecat PrebuiltUI Voice Agent")

webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/client/")


@app.post("/start")
async def start_bot():
    session_id = str(uuid.uuid4())
    logger.info(f"[{session_id}] /start received")
    return {
        "sessionId": session_id,
        "iceConfig": {
            "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
        },
    }


async def _run_pipeline(connection: SmallWebRTCConnection, session_id: str):
    """在后台跑 pipeline。"""
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=False,
            video_out_enabled=False,
        ),
    )

    worker, _context = build_pipeline(
        transport=transport,
        llm_base_url=LLM_BASE_URL,
        llm_api_key=LLM_API_KEY,
        llm_model=LLM_MODEL,
    )

    # 官方事件处理 — worker.rtvi 是内置 RTVIProcessor
    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info(f"[{session_id}] Client ready via RTVI")

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{session_id}] pipeline starting")

    # 手动刷新 pending app-messages（aiortc DTLS 握手可能未完成）
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        logger.info(f"[{session_id}] flushing {len(pending)} queued app-messages")
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()

    try:
        await runner.run(auto_end=False)
    except asyncio.CancelledError:
        logger.info(f"[{session_id}] pipeline cancelled")
        raise
    except Exception as e:
        logger.exception(f"[{session_id}] pipeline crashed: {e}")
        raise
    finally:
        logger.info(f"[{session_id}] pipeline done")


async def _handle_offer(
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
    session_id: str | None = None,
):
    resolved_session_id = session_id or str(uuid.uuid4())
    logger.info(f"[{resolved_session_id}] /api/offer received")

    async def on_connection(connection: SmallWebRTCConnection) -> None:
        # ⭐ 用 background_tasks 跑 pipeline,不阻塞 HTTP 回复
        background_tasks.add_task(_run_pipeline, connection, resolved_session_id)

    answer = await webrtc_handler.handle_web_request(
        request=request,
        webrtc_connection_callback=on_connection,
    )
    return answer


async def _handle_ice_patch(request: SmallWebRTCPatchRequest):
    logger.debug(f"ICE patch: pc_id={request.pc_id} candidates={len(request.candidates)}")
    try:
        from aiortc.sdp import candidate_from_sdp

        peer_connection = webrtc_handler._pcs_map.get(request.pc_id)
        if not peer_connection:
            raise HTTPException(status_code=404, detail="Peer connection not found")
        for c in request.candidates:
            candidate = candidate_from_sdp(c.candidate)
            candidate.sdpMid = c.sdp_mid
            candidate.sdpMLineIndex = c.sdp_mline_index
            await peer_connection.add_ice_candidate(candidate)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"ICE patch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 两个路径:直 /api/offer 和 /sessions/{sessionId}/api/offer
@app.post("/api/offer")
async def offer(
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    return await _handle_offer(request, background_tasks)


@app.post("/sessions/{session_id}/api/offer")
async def offer_session(
    session_id: str,
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    return await _handle_offer(request, background_tasks, session_id=session_id)


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    return await _handle_ice_patch(request)


@app.patch("/sessions/{session_id}/api/offer")
async def ice_candidate_session(session_id: str, request: SmallWebRTCPatchRequest):
    return await _handle_ice_patch(request)


app.mount("/client", PipecatPrebuiltUI, name="client")


def main() -> None:
    import uvicorn

    logger.info(f"Pipecat PrebuiltUI: http://{HOST}:{PORT}/")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
