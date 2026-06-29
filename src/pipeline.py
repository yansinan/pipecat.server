"""
Pipeline configuration for the Pipecat voice agent.

Uses the modern (>=1.3.0) Worker / PipelineWorker API.
Default transport: FastAPI WebSocket (self-hosted server, no API key).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService

from src.services.edge_tts import EdgeTTSService
from src.services.whisper_stt import WhisperSTTService

if TYPE_CHECKING:
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.transports.base_transport import BaseTransport


def build_pipeline(
    *,
    transport: BaseTransport,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "deepseek-v4-flash",
    whisper_model_size: str = "small",
    tts_voice: str = "zh-CN-XiaoxiaoNeural",
) -> tuple[PipelineWorker, LLMContext]:
    """Assemble the full voice-agent pipeline.

    Order: input() → STT → user_agg → LLM → TTS → output() → assistant_agg
    """
    vad = SileroVADAnalyzer()
    stt = WhisperSTTService(model_size=whisper_model_size)

    llm = OpenAILLMService(
        api_key=llm_api_key or os.environ.get("LLM_API_KEY", "fuckkey"),
        base_url=llm_base_url or os.environ.get(
            "LLM_BASE_URL",
            "http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/",
        ),
        model=llm_model,
        settings=OpenAILLMService.Settings(
            system_instruction=(
                "You are a helpful voice assistant. "
                "Keep responses concise and conversational. "
                "Avoid markdown, bullet points, or anything that can't be spoken aloud. "
                "Respond in the same language the user speaks."
            ),
            temperature=0.7,
            max_tokens=512,
        ),
    )

    tts = EdgeTTSService(voice=tts_voice)

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=vad),
    )

    processors: list[FrameProcessor] = [
        transport.input(),
        stt,
        user_agg,
        llm,
        tts,
        transport.output(),
        assistant_agg,
    ]
    pipeline = Pipeline(processors)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    return worker, context


def build_websocket_transport(
    host: str = "0.0.0.0",
    port: int = 8765,
) -> BaseTransport:
    """Self-hosted WebSocket transport via FastAPI + uvicorn.

    Client connects via browser console or ws tool.
    """
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    return FastAPIWebsocketTransport(
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
        host=host,
        port=port,
    )
