"""
Pipecat Voice Agent — PrebuiltUI (PipecatClient 4-transport) + SmallWebRTC.

Copy to src/server_prebuilt.py and customize _run_pipeline with your
STT/LLM/TTS.  Verified against pipecat 1.4.0 + pipecat-ai-prebuilt 1.0.3.

PACKAGE WARNING:
  pipecat-ai-small-webrtc-prebuilt (2.5.0) ships a STALE client bundle.
  Use pipecat-ai-prebuilt (1.0.3) instead.  Both packages mount a StaticFiles
  at /client, but only the newer one calls /start and /api/offer.

CLEANUP vs OLDER TEMPLATES:
  - No bare `/api/offer` (POST/PATCH) — client only calls `/sessions/{id}/api/offer`
  - No `_handle_ice_patch()` reimplementation — `handler.handle_patch_request()` does it
  - No `session_id` UUID plumbing — `connection.pc_id` is what we log against
  - No `import logging` / `logging.basicConfig` — loguru owns logging
  - No CORS middleware — same-origin (PrebuiltUI mounted on same FastAPI)
  - WorkerRunner import at top-level, not inside _run_pipeline

See SKILL.md pitfall #25 (route shrinking) and #29 (framework first, custom second)
for the rationale. The companion vanilla-JS template (`bot-js-client-server.py`)
keeps CORS + bare `/api/offer` because that client really does POST cross-port.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
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
from pipecat.workers.runner import WorkerRunner
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

# Replace with your pipeline:
# from src.pipeline import build_pipeline

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8766"))
HOST = os.environ.get("HOST", "0.0.0.0")

# LLM config — defaults point at self-hosted LiteLLM/Headroom
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL", "http://your-litellm-host/litellm/headroom"
)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "sk-placeholder")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

app = FastAPI(title="Pipecat PrebuiltUI Voice Agent")

webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/client/")


@app.post("/start")
async def start_bot():
    """Must return sessionId (camelCase) + iceConfig.  Client's startBot()
    reads these; missing either causes "authenticating" → hang."""
    session_id = str(uuid.uuid4())
    logger.info(f"[{session_id}] /start received")
    return {
        "sessionId": session_id,
        "iceConfig": {
            "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
        },
    }


async def _run_pipeline(connection: SmallWebRTCConnection) -> None:
    """Run pipeline in background.  Called via background_tasks.add_task()."""
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=False,
            video_out_enabled=False,
        ),
    )
    # build_pipeline uses keyword-only (*, transport=...) — pass keyword:
    worker, _ctx = build_pipeline(
        transport=transport,
        llm_base_url=LLM_BASE_URL,
        llm_api_key=LLM_API_KEY,
        llm_model=LLM_MODEL,
    )

    # Greeting via RTVI client-ready event (NOT @transport.event_handler —
    # that one is Daily-only; SmallWebRTC fires on_client_ready via RTVI).
    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info(f"[{connection.pc_id}] Client ready via RTVI")

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{connection.pc_id}] pipeline starting")

    # Flush pending app-messages — aiortc DTLS handshake may stall in cloud
    # browsers, leaving is_connected()==False and queuing data-channel msgs
    # forever. See SKILL.md pitfall #24.
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        logger.info(
            f"[{connection.pc_id}] flushing {len(pending)} queued app-messages"
        )
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()

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


# Client (SmallWebRTCTransport 1.10+) only calls /sessions/{id}/api/offer
# — bare /api/offer was for pre-1.10 transports.  See SKILL.md pitfall #25.
@app.post("/sessions/{session_id}/api/offer")
async def offer_session(
    session_id: str,
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    logger.info(f"[{session_id}] /api/offer received")

    async def on_connection(connection: SmallWebRTCConnection) -> None:
        # MUST use background_tasks — direct await blocks HTTP response
        background_tasks.add_task(_run_pipeline, connection)

    return await webrtc_handler.handle_web_request(
        request=request, webrtc_connection_callback=on_connection,
    )


# Framework's handle_patch_request handles ICE candidate registration
# correctly.  Do NOT reimplement via _pcs_map + candidate_from_sdp —
# see SKILL.md pitfall #29.
@app.patch("/sessions/{session_id}/api/offer")
async def ice_candidate_session(
    session_id: str,
    request: SmallWebRTCPatchRequest,
):
    return await webrtc_handler.handle_patch_request(request)


app.mount("/client", PipecatPrebuiltUI, name="client")


def main() -> None:
    import uvicorn
    logger.info(f"Pipecat PrebuiltUI: http://{HOST}:{PORT}/")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()