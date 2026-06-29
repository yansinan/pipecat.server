"""
Pipecat Voice Agent — main entry point.

Runs a FastAPI server with a WebSocket endpoint at /conversation.
Use:
    ./run.sh

Then connect a WebSocket client to ws://localhost:8765/conversation
and exchange MCP-style messages (JSON with audio frames).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger
import uvicorn

from pipecat.frames.frames import LLMRunFrame, EndFrame
from pipecat.workers.runner import WorkerRunner
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from src.pipeline import build_pipeline

load_dotenv(override=True)

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "0.0.0.0")

app = FastAPI(title="Pipecat Voice Agent")


@app.websocket("/conversation")
async def conversation(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket client connected")

    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=None,  # VAD handled in pipeline
        ),
    )
    worker, context = build_pipeline(
        transport=transport,
        whisper_model_size=os.environ.get("WHISPER_MODEL_SIZE", "small"),
        tts_voice=os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
        llm_model=os.environ.get("LLM_MODEL", "minimax"),
    )

    context.add_message({
        "role": "developer",
        "content": "你好，我是你的语音助手。简短回答即可。",
    })

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await worker.queue_frames([LLMRunFrame()])

    try:
        await runner.run()
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        await worker.cancel()


def main():
    logging.basicConfig(level=logging.WARNING)
    logger.info(f"Starting WebSocket server on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
