"""Minimal Pipecat server compatible with the vanilla JS client (client/javascript/).

Usage:
    pip install pipecat-ai[webrtc,silero]
    python bot-js-client-server.py
    # JS client: http://localhost:5173/ (npm run dev in client/javascript/)
    # /start endpoint: http://localhost:7860/start
"""
from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

load_dotenv(override=True)
PORT = int(os.environ.get("PORT", "7860"))

app = FastAPI()

# ⭐ CORS: JS client runs on a different port (5173) -> cross-origin POST
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

handler = SmallWebRTCRequestHandler(
    ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")],
)

# ── Test audio cache + active pipeline references ──
_TEST_AUDIO: bytes | None = None
_active_inbounds: dict[str, object] = {}


def _get_test_audio() -> bytes:
    """Generate/cache test audio: 1.5s, 220Hz→sweep, 16kHz 16-bit mono PCM."""
    global _TEST_AUDIO
    if _TEST_AUDIO is not None:
        return _TEST_AUDIO
    import math, struct
    sr = 16000
    buf = bytearray()
    for i in range(int(sr * 0.3)):
        buf += struct.pack("<h", int(math.sin(2 * math.pi * 220 * i / sr) * 300))
    for i in range(int(sr * 0.8)):
        f = 280 + 500 * math.sin(2 * math.pi * 2 * i / sr)
        buf += struct.pack("<h", int(math.sin(2 * math.pi * f * i / sr) * (14000 + 2000 * math.sin(2 * math.pi * 3 * i / sr))))
    for i in range(int(sr * 0.4)):
        buf += struct.pack("<h", 0)
    _TEST_AUDIO = bytes(buf)
    return _TEST_AUDIO


def build_pipeline(transport):
    """Replace this with your pipeline builder."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frameworks.rtvi import RTVIProcessor, RTVIObserver
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    # ⭐ Use settings= to avoid deprecation warning
    llm = OpenAILLMService(
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        settings=OpenAILLMService.Settings(
            model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            temperature=0.7,
            max_tokens=512,
        ),
    )

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        RTVIProcessor(),
        # stt...
        user_agg,
        llm,
        # BotTextProcessor (for transcription forwarding)
        # tts...
        transport.output(),
        assistant_agg,
    ])
    worker = PipelineWorker(
        pipeline,
        observers=[RTVIObserver()],
        params=PipelineParams(enable_metrics=True),
    )

    # ⭐ Greeting: must use @worker.rtvi.event_handler, NOT @transport.event_handler
    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        context.add_message({
            "role": "developer",
            "content": "Hello, I am your voice assistant. I'll introduce myself and answer questions briefly."
        })
        from pipecat.frames.frames import LLMRunFrame
        await worker.queue_frames([LLMRunFrame()])

    return worker, context


# ⭐ Protocol: POST /start -> get sessionId
@app.post("/start")
async def start():
    return {"sessionId": str(uuid.uuid4())}


async def _run(connection: SmallWebRTCConnection, sid: str):
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    # Register for test audio injection
    _active_inbounds[sid] = transport.input()

    worker, ctx = build_pipeline(transport)
    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner()
    await runner.add_workers(worker)
    try:
        await runner.run(auto_end=False)
    finally:
        _active_inbounds.pop(sid, None)


async def _offer(
    request: SmallWebRTCRequest,
    tasks: BackgroundTasks,
    session_id: str | None = None,
):
    sid = session_id or str(uuid.uuid4())

    async def on_conn(c: SmallWebRTCConnection):
        tasks.add_task(_run, c, sid)

    return await handler.handle_web_request(request, on_conn)


async def _ice(request: SmallWebRTCPatchRequest):
    from aiortc.sdp import candidate_from_sdp

    pc = handler._pcs_map.get(request.pc_id)
    if not pc:
        raise HTTPException(404)
    for c in request.candidates:
        cand = candidate_from_sdp(c.candidate)
        cand.sdpMid, cand.sdpMLineIndex = c.sdp_mid, c.sdp_mline_index
        await pc.add_ice_candidate(cand)


@app.post("/api/offer")
async def offer_direct(request: SmallWebRTCRequest, tasks: BackgroundTasks):
    return await _offer(request, tasks)


@app.post("/sessions/{session_id}/api/offer")
async def offer_session(
    session_id: str, request: SmallWebRTCRequest, tasks: BackgroundTasks
):
    return await _offer(request, tasks, session_id)


@app.patch("/api/offer")
async def ice_direct(request: SmallWebRTCPatchRequest):
    return await _ice(request)


@app.patch("/sessions/{session_id}/api/offer")
async def ice_session(session_id: str, request: SmallWebRTCPatchRequest):
    return await _ice(request)


# ── Test audio endpoints ──


@app.get("/test.pcm")
async def serve_test_audio():
    return Response(content=_get_test_audio(), media_type="application/octet-stream")


@app.post("/inject_test_audio/{session_id}")
async def inject_test_audio(session_id: str):
    inbound = _active_inbounds.get(session_id)
    if not inbound:
        raise HTTPException(404, detail="Session not found")
    data = _get_test_audio()
    frame = InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
    await inbound.push_frame(frame)
    return {"status": "ok", "bytes": len(data)}


@app.post("/inject_test_audio")
async def inject_test_audio_latest():
    if not _active_inbounds:
        raise HTTPException(404, detail="No active sessions")
    keys = list(_active_inbounds.keys())
    return await inject_test_audio(keys[-1])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
