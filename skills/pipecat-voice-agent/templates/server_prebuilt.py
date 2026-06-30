"""
Pipecat Voice Agent — 官方 PrebuiltUI (pipecat-ai-prebuilt 1.0.3) + SmallWebRTC.

Known-good server template. Verified 2026-06-30 with both aiortc e2e probe
AND real browser test (Client READY / Agent READY, audio + bot text flowing).

End points (all required for the real PrebuiltUI client):
  GET   /                            → 307 redirect to /client/
  POST  /start                       → startBot entry, mints sessionId
  POST  /api/offer                   → SDP exchange, runs handle_web_request
  POST  /sessions/{session_id}/api/offer  → same, with sessionId in path
  PATCH /api/offer                   → ICE candidate updates
  PATCH /sessions/{session_id}/api/offer  → same, with sessionId in path
  MOUNT /client/*                    → 官方 PrebuiltUI static (pipecat-ai-prebuilt 1.0.3)

Run:
  PORT=8766 uv run --project . python -m src.server_prebuilt
  open http://localhost:8766/

Pitfalls already handled (see SKILL.md Pitfall section):
  - Mount order: API routes first, static mount last
  - STUN server included for cross-NAT ICE
  - Do NOT set audio_*_sample_rate in TransportParams (use defaults — EdgeTTS 24kHz
    and Whisper 16kHz get handled by aiortc automatically)
  - Returns sessionId + iceConfig (camelCase) — client expects this exact shape
  - Registers BOTH /api/offer and /sessions/{id}/api/offer
  - Uses TransportParams (NOT the non-existent SmallWebRTCTransportParams)
  - build_pipeline(transport=transport) — keyword-only signature
  - pipeline runs in background_tasks.add_task(), HTTP returns SDP immediately
  - WorkerRunner wraps the worker (direct worker.run() crashes — missing params)
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

# Real PrebuiltUI client bundle. Do NOT use pipecat-ai-small-webrtc-prebuilt
# (its bundled JS is broken/old — Daily-flavoured, no startBot flow).
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

# Replace with your own pipeline composition.
from src.pipeline import build_pipeline

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8766"))
HOST = os.environ.get("HOST", "0.0.0.0")

logging.basicConfig(level=logging.WARNING)

app = FastAPI(title="Pipecat PrebuiltUI Voice Agent")


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/client/")


# STUN for cross-NAT ICE.
webrtc_handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)


@app.post("/start")
async def start_bot():
    """Mints sessionId. PrebuiltUI's startBot() calls this first.

    CRITICAL: client expects camelCase `sessionId` + `iceConfig.iceServers`,
    NOT snake_case `pc_id`. Returning the wrong shape silently fails —
    client never calls /api/offer and the page just shows "loading".
    """
    session_id = str(uuid.uuid4())
    logger.info(f"[{session_id}] /start received")
    return {
        "sessionId": session_id,
        "iceConfig": {
            "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
        },
    }


async def _run_pipeline(connection: SmallWebRTCConnection, session_id: str):
    """Background pipeline runner. SDP answer returns IMMEDIATELY; this
    runs to completion in the background."""
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Do NOT pin sample_rate here. EdgeTTS outputs 24kHz MP3 and
            # Whisper ingests 16kHz; aiortc handles the conversion. Pinning
            # causes audio stutter because non-integer-ratio resampling
            # falls back to a poor default.
            video_in_enabled=False,
            video_out_enabled=False,
        ),
    )
    worker, _context = build_pipeline(transport=transport)  # keyword-only!
    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)
    logger.info(f"[{session_id}] pipeline starting")
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
    resolved = session_id or str(uuid.uuid4())
    logger.info(f"[{resolved}] /api/offer received")

    async def on_connection(connection: SmallWebRTCConnection) -> None:
        # CRITICAL: background_tasks.add_task — do NOT await runner.run()
        # here. Awaiting it inline blocks the HTTP handler until the
        # pipeline ends (forever, in practice), and curl/browser time out
        # with no SDP answer.
        background_tasks.add_task(_run_pipeline, connection, resolved)

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


# BOTH path forms — client uses /sessions/{id}/api/offer, but some
# integrations also hit the unprefixed /api/offer.
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


# Static mount LAST — must be after /api/offer registration.
app.mount("/client", PipecatPrebuiltUI, name="client")


def main() -> None:
    import uvicorn

    logger.info(f"Pipecat PrebuiltUI: http://{HOST}:{PORT}/")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
