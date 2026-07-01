"""
Pipecat Voice Agent Pipeline — 官方 PrebuiltUI (pipecat-ai-prebuilt) + SmallWebRTC.

Order: input() → STT → user_agg → LLM → TTS → output() → assistant_agg
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from src.services.edge_tts import EdgeTTSService
from src.services.llm import HeadroomLLMService
from src.services.whisper_stt import WhisperSTTService

if TYPE_CHECKING:
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
    from dotenv import load_dotenv

    load_dotenv(override=True)

    # --- Services ---
    stt = WhisperSTTService(model_size=whisper_model_size)

    llm = HeadroomLLMService(
        base_url=llm_base_url or os.environ.get("LLM_BASE_URL", ""),
        api_key=llm_api_key or os.environ.get("LLM_API_KEY", ""),
        settings=OpenAILLMService.Settings(
            model=llm_model or os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            temperature=0.7,
            max_tokens=512,
        ),
    )

    tts = EdgeTTSService(voice=tts_voice)

    # --- Context & Aggregators ---
    # VAD 触发 turn start（用户开口即触发）。
    # SmartTurn V3 决定 turn end（用户是否真说完了 — 避免抢答）。
    #
    # barge-in (用户说话打断 bot TTS)：
    #   VADUserTurnStartStrategy 继承 BaseUserTurnStartStrategy，
    #   enable_interruptions 默认 True — 用户开口时 bot 的 TTS 立即停止。
    #   SmartTurn V3 不参与打断，只决定 turn end。
    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[TurnAnalyzerUserTurnStopStrategy(
                    turn_analyzer=LocalSmartTurnAnalyzerV3(),
                )],
            ),
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # --- Pipeline ---
    # 官方结构: input() → user_agg → llm → output() → assistant_agg
    # 我们加 STT 和 TTS 作为独立服务
    processors = [
        transport.input(),
        stt,
        user_agg,
        llm,
        LLMTextProcessor(),
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
    """Build a FastAPI WebSocket transport for the RawPCM path."""
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
