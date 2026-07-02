"""
Pipecat Voice Agent Pipeline — 官方 PrebuiltUI (pipecat-ai-prebuilt) + SmallWebRTC.

Order: input() → STT → user_agg → LLM → TTS → output() → assistant_agg
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

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
from pipecat.processors.frameworks.rtvi import (
    BotOutputTransformResult,
    RTVIObserverParams,
)
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_start.wake_phrase_user_turn_start_strategy import WakePhraseUserTurnStartStrategy
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from src.services.edge_tts import EdgeTTSService
from src.services.llm import HeadroomLLMService
from src.services.natural_language_recorder import NaturalLanguageRecorder
from src.services.whisper_stt import WhisperSTTService

# ─── Wake phrases ──────────────────────────────────────────────
# The user must say one of these to trigger the bot. Official
# WakePhraseUserTurnStartStrategy uses re.escape (exact match, not fuzzy),
# so we enumerate phonetic variants empirically known from Whisper output.
#
# - English: "hermes" and close phonetic variants
# - Chinese: "小智" and 同音字 (小芝/小志/小致), 谐音 (晓智/晓之/小之),
#   近义词 (知晓/晓得), and pinyin form (Whisper sometimes outputs pinyin)
DEFAULT_WAKE_PHRASES = (
    "hermes|hermis|harmes|赫米斯|赫耳墨斯|"
    "小智|小芝|小志|小致|晓智|晓之|小之|知晓|晓得|"
    "xiao zhi|xiazhi"
)
WAKE_PHRASES = [p.strip() for p in os.environ.get("WAKE_PHRASES", DEFAULT_WAKE_PHRASES).split("|") if p.strip()]
WAKE_TIMEOUT = float(os.environ.get("WAKE_TIMEOUT", "10.0"))

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
    language_recorder: NaturalLanguageRecorder | None = None,
) -> tuple[PipelineWorker, LLMContext]:
    """Assemble the full voice-agent pipeline.

    Order: input() → STT → user_agg → LLM → TTS → output() → assistant_agg

    If `language_recorder` is provided, all natural language from three sources
    (STT = ambient, PrebuiltUI text = user, bot output = bot) lands in
    `~/.local/share/pipecat/transcripts/YYYY-MM-DD.md`.
    """
    from datetime import datetime as _dt
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
                start=[
                    # 唤醒词 gate — 必须在第一位，gates VAD 和后续 strategy
                    # 默认要求用户说 hermes/小智 等（见 WAKE_PHRASES 常量）。
                    # 命中后保持 AWAKE 状态 WAKE_TIMEOUT 秒，期间 VAD 可正常触发。
                    WakePhraseUserTurnStartStrategy(
                        phrases=WAKE_PHRASES,
                        timeout=WAKE_TIMEOUT,
                    ),
                    VADUserTurnStartStrategy(),
                ],
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

    # -------------------------------------------------------
    # RTVI Observer 配置：全开，方便前端调试
    # - vad_user_speaking_enabled：直传 VAD 信号（不经过 turn strategy）
    # - metrics_enabled：发 metrics 数据到前端（TTFB、token 用量等）
    # - bot_audio_level_enabled：bot 音量数据
    # - bot_output_transforms：发到客户端前去除所有 emoji（NSRegularExpression）
    #   因为 edge-tts 把中文符号转为 emoji 后会污染 UI
    # -------------------------------------------------------
    import re
    _EMOJI_PATTERN = re.compile(
        "[\U0001F300-\U0001F9FF"  # symbols & pictographs
        "\U0001FA00-\U0001FAFF"  # symbols & pictographs extended-A
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U00002600-\U000026FF"  # miscellaneous symbols
        "\U00002700-\U000027BF"  # dingbats
        "]+",
        flags=re.UNICODE,
    )

    async def _strip_emoji(text, agg_type, accumulated_text=None, remaining_text=None):
        cleaned = _EMOJI_PATTERN.sub("", text).strip()
        if accumulated_text is not None and remaining_text is not None:
            ratio = len(accumulated_text) / max(len(text), 1)
            split = int(ratio * len(cleaned))
            return BotOutputTransformResult(
                text=cleaned,
                accumulated_text=cleaned[:split],
                remaining_text=cleaned[split:],
            )
        return BotOutputTransformResult(text=cleaned)

    rtvi_params = RTVIObserverParams(
        bot_output_enabled=True,
        bot_llm_enabled=True,
        bot_tts_enabled=True,
        bot_speaking_enabled=True,
        bot_audio_level_enabled=True,
        user_llm_enabled=True,
        user_speaking_enabled=True,
        vad_user_speaking_enabled=True,    # 直传 VAD 信号，调试看到底谁是触发源
        user_transcription_enabled=True,
        user_audio_level_enabled=True,
        metrics_enabled=True,               # 推 metrics 到前端
        audio_level_period_secs=0.1,
        system_logs_enabled=True,
        bot_output_transforms=[("*", _strip_emoji)],
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        # 5 分钟无交互后不自动断开（默认 IDLE_TIMEOUT_SECS=300）
        cancel_on_idle_timeout=False,
        rtvi_observer_params=rtvi_params,
    )

    # ─── Natural language recording ─────────────────────────────
    # Three sources, one recorder:
    #   - STT finalized transcription → role="ambient"
    #   - PrebuiltUI text input → role="user"
    #   - Bot finalized text → role="bot"
    if language_recorder is not None:
        @user_agg.event_handler("on_user_turn_message_added")
        async def _log_user_message_added(aggregator, message):
            """Fired when a user message is written to the LLM context.

            Covers BOTH STT (ambient speech) and send-text (typed input).
            user_id field distinguishes:
              - "ambient"  → STT pickup
              - None/other  → typed input
            """
            text = (getattr(message, "content", None) or "").strip()
            if not text:
                return
            user_id = getattr(message, "user_id", None)
            role = "ambient" if user_id == "ambient" else "user"
            ts_raw = getattr(message, "timestamp", None)
            try:
                ts = _dt.fromisoformat(ts_raw) if ts_raw else _dt.now()
            except (TypeError, ValueError):
                ts = _dt.now()
            await language_recorder.write(role, text, timestamp=ts)

        @worker.rtvi.event_handler("on_client_message")
        async def _log_typed_input(rtvi, msg):
            """Catch-all: PrebuiltUI RTVI messages that aren't user-turns.

            Most typed input flows through LLMMessagesAppendFrame and triggers
            on_user_turn_message_added. This handler is a safety net for
            non-standard RTVI message types that contain text the user typed.
            """
            # Send-text goes through _handle_send_text → LLMMessagesAppendFrame,
            # which fires on_user_turn_message_added. The general
            # on_client_message event does NOT fire for send-text.
            # We keep this for future RTVI message types (e.g., describe-image).
            logger.debug(f"[recorder] rtvi msg type={getattr(msg, 'type', None)!r} — already handled by on_user_turn_message_added")

        # Bot output — emitted via RTVIObserver on the rtvi observer.
        # RTVI's event name in 1.4.0 for finalized assistant text.
        @worker.rtvi.event_handler("on_bot_output")
        async def _log_bot_output(rtvi, msg):
            """Bot finalized an assistant reply."""
            # msg is a BotOutputMessage dataclass — see pipecat/frames/frames.py
            text = getattr(msg, "text", None)
            if not text or not str(text).strip():
                return
            await language_recorder.write("bot", str(text).strip(), timestamp=_dt.now())

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
