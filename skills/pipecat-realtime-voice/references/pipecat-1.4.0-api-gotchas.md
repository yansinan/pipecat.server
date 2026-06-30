# Pipecat 1.4.0 API Gotchas

## TranscriptionFrame / InterimTranscriptionFrame (MUST provide user_id + timestamp)

pipecat 1.4.0 changed these frames to require `user_id` (str) and `timestamp` (str) at construction.
Omitting them causes a fatal crash: `InterimTranscriptionFrame.__init__() missing 1 required positional argument: 'timestamp'`

```python
# BROKEN (pipecat <1.4):
yield InterimTranscriptionFrame(seg.text, 1.0)

# FIX:
import time
yield InterimTranscriptionFrame(
    text=seg.text,
    user_id="user",
    timestamp=str(time.time()),
)
```

Applies to **both** `TranscriptionFrame` and `InterimTranscriptionFrame`.
Your STT service (Deepgram, Whisper, AssemblyAI) must emit these — the crash surfaces
when a user speaks and the STT sends a frame.

## PipelineWorker requires `observers=[RTVIObserver()]` for SmallWebRTC

Without this you get: `RTVIProcessor found in pipeline but no RTVIObserver in observers.`
Browser shows "authenticating" → "disconnected".

```python
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(enable_metrics=True),
    observers=[RTVIObserver()],     # ← required when RTVIProcessor is in pipeline
)
```

## RTVIProcessor required for SmallWebRTC (not needed for Daily)

SmallWebRTCTransport does NOT have built-in RTVI handling (DailyTransport does).
Always add `RTVIProcessor` to the pipeline processors list when using SmallWebRTC:

```python
from pipecat.processors.frameworks.rtvi import RTVIProcessor, RTVIObserver

processors: list[FrameProcessor] = [
    transport.input(),
    RTVIProcessor(),       # ← required for SmallWebRTC
    stt,
    user_agg,
    llm,
    tts,
    transport.output(),
    assistant_agg,
]
```

## `on_client_ready` callback for bot-initiated conversation

Without this, the bot stays silent until the user speaks first.
Reference: `pipecat-ai/pipecat-examples/simple-chatbot/server/bot-openai.py`

```python
@transport.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    context.add_message({"role": "developer", "content": "Start by introducing yourself."})
    await worker.queue_frames([LLMRunFrame()])
```

For SmallWebRTC (no `event_handler`), use RTVIProcessor's observer pattern
or queue an initial LLMRunFrame in the on_connection callback.

## background_tasks.add_task() required (don't await pipeline in callback)

Runner's `run()` is a blocking coroutine. The `/api/offer` endpoint needs
to return the SDP answer (from `handle_web_request`) before the pipeline
starts, because the client is waiting for that answer.

```python
# BROKEN — blocks HTTP /api/offer forever:
async def on_connection(connection):
    await runner.run()     # ← Never returns, HTTP handler hangs

# FIX — let the HTTP response return immediately:
async def on_connection(connection):
    background_tasks.add_task(_run_pipeline, connection, session_id)
```

## build_pipeline() uses keyword-only args

```python
def build_pipeline(*, transport: BaseTransport, ...) -> tuple[PipelineWorker, LLMContext]:
```
Caller MUST use keyword form: `build_pipeline(transport=transport)`
Positional: `build_pipeline(transport)` → `takes 0 positional arguments but 1 was given`

## Dual route: /api/offer AND /sessions/{session_id}/api/offer

The PrebuiltUI client sends SDP offers to BOTH paths. Both need POST and PATCH routes.

## /start response must be camelCase

Browser client expects: `{"sessionId": "...", "iceConfig": {"iceServers": [...]}}`
Using `pc_id` or `session_id` (snake_case) → client stays "authenticating" then disconnects.

## VAD（SileroVADAnalyzer）required for STT to trigger

Without VAD in LLMUserAggregatorParams, STTService.run_stt() never fires.
The pipeline stays idle — no transcription frames reach the client.

```python
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.audio.vad.silero import SileroVADAnalyzer

# FIX — VAD tells the pipeline when user speech ends:
user_agg, assistant_agg = LLMContextAggregatorPair(
    context,
    params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),  # ← required
    ),
)
```

VAD signals the frame pipeline that user speech has ended, which tells STT
to run transcription. Without it, run_stt() only fires on timeout (60s default),
if at all.  This is the #1 cause of "no text appears in chat".

## EdgeTTS 24kHz + SmallWebRTC 48kHz: AudioResampleProcessor

EdgeTTSService outputs 24kHz PCM. SmallWebRTCTransport encodes to Opus (48kHz).
The 24→48 non-integer resample via aiortc's default SRC **can** produce audible
stutter, clicking, or metallic artifacts.

**Do NOT set audio_out_sample_rate=16000** in TransportParams — that forces
24→16 downsampling which sounds worse. Official example doesn't set it:

```python
TransportParams(audio_in_enabled=True, audio_out_enabled=True)
```

If stutter persists, add AudioResampleProcessor in the pipeline between TTS
and transport.output():

```python
from pipecat.processors.filters import AudioResampleProcessor

processors = [
    ...  # tts before this
    AudioResampleProcessor(src_sample_rate=24000, dst_sample_rate=48000),
    transport.output(),
    ...
]
```

This gives control over resample quality (soxr/libsamplerate) rather than
relying on aiortc's internal SRC.

## Bot-initiated conversation via on_client_ready

Without this, the bot stays silent until the user speaks first.  Reference:
`pipecat-ai/pipecat-examples/simple-chatbot/server/bot-openai.py`:

```python
# DailyTransport version:
@transport.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    context.add_message({"role": "developer", "content": "Start by introducing yourself."})
    await worker.queue_frames([LLMRunFrame()])

# SmallWebRTC version (no event_handler — queue in on_connection):
async def on_connection(connection):
    context.add_message({"role": "developer", "content": self._system_prompt})
    await worker.queue_frames([LLMRunFrame()])
```

## BotTextProcessor: send assistant text to browser chat bubble

RTVIProcessor sends `bot-ready` and handles `client-ready`, but does **NOT** automatically
send assistant transcription text to the browser's chat display. The TTS audio goes through
WebRTC (you hear it), but no text appears in the chat bubble unless you explicitly push it
through the data channel via `OutputTransportMessageFrame`.

### ⚠️ Placement: MUST be BEFORE TTS (between LLM and TTS), NOT after

`TTSTextFrame` is **consumed** by the TTS service — it never reaches downstream processors.
Placing BotTextProcessor after TTS means it will never receive the frame.

**Wrong (frame never reaches BotTextProcessor):**
```python
processors = [tts, BotTextProcessor(), output]   # TTSTextFrame consumed by tts
```

**Correct:**
```python
processors = [..., llm, BotTextProcessor(), tts, ...]
```

### ⚠️ `_check_started` — double-underscore name mangling

`FrameProcessor._check_started()` checks `self.__started` which Python name-mangles to
`self._FrameProcessor__started`. Setting `self._started = True` in the subclass creates
a NEW unrelated attribute and does NOT bypass the check. The fix:

```python
def __init__(self):
    super().__init__()
    self._FrameProcessor__started = True
```

### ⚠️ Push order: original TTSTextFrame first, then OutputTransportMessageFrame

Inside `process_frame`, push the original frame to TTS FIRST, then push the
OutputTransportMessageFrame. Reversing the order stalls the pipeline — TTS never
receives the text and no audio is generated.

```python
# Correct order:
await self.push_frame(frame, direction)                 # 1st: TTS gets text → audio
await self.push_frame(OutputTransportMessageFrame(...), UPSTREAM)  # 2nd: text to browser
```

### Full implementation

```python
from pipecat.frames.frames import (
    Frame,
    TTSTextFrame,
    OutputTransportMessageFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class BotTextProcessor(FrameProcessor):
    """Send assistant text through RTVI data channel for browser chat display."""

    def __init__(self):
        super().__init__()
        self._FrameProcessor__started = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, TTSTextFrame):
            import json
            msg = json.dumps({
                "type": "bot-transcription",
                "data": {"text": str(frame), "user_id": "assistant"},
            })
            await self.push_frame(frame, direction)  # 1st: TTS gets text
            await self.push_frame(                     # 2nd: text to browser
                OutputTransportMessageFrame(message=msg),
                FrameDirection.UPSTREAM,
            )
        else:
            await self.push_frame(frame, direction)


# In pipeline — MUST be BETWEEN LLM and TTS:
processors = [
    transport.input(),
    RTVIProcessor(),
    stt,
    user_agg,
    llm,
    BotTextProcessor(),        # ← HERE, before TTS, not after
    tts,
    transport.output(),
    assistant_agg,
]
```

Without this, the browser shows an empty chat bubble and the user says "no text回复".

See `references/prebuilt-text-echo-bot-text-processor.md` for a dedicated reference
with additional explanation of the upstream direction and verification steps.

## Official example alignment methodology

When diagnosing server-side issues against pipecat-examples, follow this structured
alignment rather than guessing:

1. **Identify the transport** (Daily ↔ SmallWebRTC ↔ WebSocket) — each has different
   requirements for RTVI handling, event callbacks, and VAD.
2. **VAD → ASR → LLM → TTS** — examine each stage separately, with official source
   citation for every parameter. All examples are under `pipecat-ai/pipecat-examples/`
   and `pipecat-ai/small-webrtc-prebuilt/test/`.
3. **Pipeline processors list** — compare exact order and what's between each stage.
4. **Entry point** (`main()` → `bot()` vs `uvicorn.run()`) — affects background task handling.
5. For edge-TTS specific config, go to `rany2/edge-tts` directly (not pipecat),
   since pipecat does not ship a bundled EdgeTTSService. The service wraps
   edge-tts via a custom `src/services/edge_tts.py` with ffmpeg decode.

## Package choice: pipecat-ai-prebuilt NOT pipecat-ai-small-webrtc-prebuilt

| Package | Version | Verdict |
|---|---|---|
| `pipecat-ai-small-webrtc-prebuilt` | 2.5.0 | ❌ Stale JS (Daily-based, no /api/offer) |
| `pipecat-ai-prebuilt` | 1.0.3 | ✅ Upstream (startBot + /api/offer) |

## Reference: official example architecture

`pipecat-ai/pipecat-examples/simple-chatbot` — complete reference with 3 transport types:

| Aspect | Official (Daily path) | SmallWebRTC path |
|---|---|---|
| Transport | DailyTransport (API key+room) | SmallWebRTCTransport (STUN only) |
| STT | Deepgram/AssemblyAI (API key) | Local Whisper (free) |
| TTS | ElevenLabs (API key) | EdgeTTS (free) |
| RTVI | Built into DailyTransport | RTVIProcessor |
| Entry | `RunnerArguments` + `bot()` | `main()` + `uvicorn.run()` |
