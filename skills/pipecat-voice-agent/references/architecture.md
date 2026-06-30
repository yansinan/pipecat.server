# Pipecat architecture (read once, don't re-derive)

## Frame types

- `AudioRawFrame` / `InputAudioRawFrame` / `OutputAudioRawFrame` — raw PCM (sample_rate, num_channels, audio bytes)
- `TextFrame` — text (user/bot speech, transcriptions)
- `LLMRunFrame` — trigger LLM inference
- `StartFrame` / `EndFrame` — pipeline lifecycle

## Pipeline order (canonical)

```python
pipeline = Pipeline([
    transport.input(),       # mic/WebRTC/WS → InputAudioRawFrame
    stt,                     # InputAudioRawFrame → TextFrame (transcription)
    user_aggregator,         # accumulates user turns, pushes to LLM
    llm,                     # LLM → TextFrame (bot response)
    tts,                     # TextFrame → OutputAudioRawFrame
    transport.output(),      # OutputAudioRawFrame → speaker/WebRTC/WS
    assistant_aggregator,    # accumulates assistant turns
])
```

## Worker types

- `PipelineWorker` — wraps a single pipeline, manages frame lifecycle, metrics, interruptions
- `WorkerRunner` — manages multiple workers, handles signals (SIGINT/SIGTERM), graceful shutdown

## Aggregators

- `LLMContextAggregatorPair` — creates `LLMUserAggregator` + `LLMAssistantAggregator` in one call
- `LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())` — VAD-driven turn detection

## Transport families

- `SmallWebRTCTransport` — WebRTC (aiortc), official UI at `/client/`, no system audio deps
- `DailyTransport` — Daily.co WebRTC (requires Daily API key)
- `FastAPIWebsocketTransport` — raw WebSocket (needs custom serializer + client)
- `LocalAudioTransport` — system mic/speaker (needs pyaudio + portaudio19-dev)

## When to use which

- **SmallWebRTC**: default choice. Browser handles mic, WebRTC P2P, no server audio stack.
- **Daily**: need multi-user rooms, recording, or Daily-specific features.
- **WebSocket**: custom protocol, non-browser clients, or when you want raw PCM control.
- **Local**: desktop-only testing, no browser, direct hardware access.

## Official example locations

- SmallWebRTC: `examples/transports/transports-small-webrtc.py`
- Daily: `examples/transports/transports-daily.py`
- WebSocket: `examples/transports/transports-websocket.py`
- Getting started: `examples/getting-started/01-07-*.py` (basic patterns)

## Common pitfalls

1. **Mount order**: `app.mount("/")` swallows `/api/offer`. Always mount at `/client/`.
2. **Port conflicts**: official default 7860; change to 8765 or read env var.
3. **Lifespan startup**: `async def lifespan(app: FastAPI): yield` — don't block before yield.
4. **Background process logs**: always redirect to file, never let them vanish into the void.
5. **Service init blocking**: STT/TTS first-run downloads can stall; set cache dirs into project.
