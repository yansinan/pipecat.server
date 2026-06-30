---
name: pipecat-voice-agent
description: "Set up, configure, debug, and extend Pipecat voice agents. Covers local Whisper ASR, Silero VAD (mandatory for STT triggering), Edge TTS (24kHz output — must match TransportParams), Headroom/LiteLLM LLM integration, WebSocket (RawPCM) and WebRTC (SmallWebRTC) transports, official PrebuiltUI client (pipecat-ai-prebuilt 1.0.3), headless aiortc verification, RTVI processor setup, and transcript frame constructor changes in 1.4.0."
version: 2.0.1
tags: [pipecat, voice-agent, webrtc, websocket, stt, tts, vad, prebuilt-ui, aiortc]
---

# Pipecat Voice Agent

This skill covers the voice agent at `~/workspace/pipecat/` — a Pipecat
1.4.0 pipeline running Whisper ASR + Headroom/LiteLLM LLM + Edge TTS,
exposed via two parallel transports:

- **RawPCM over WebSocket** on port 8765 (browser-side `AudioContext`
  pipeline, custom JS test page, no server-side client bundle)
- **Official PrebuiltUI over WebRTC** on port 8766 (React client with
  4-transport switcher from `pipecat-ai-prebuilt` 1.0.3)

When to load this skill:
- Wiring a new transport (WebRTC / Daily / WebSocket / Twilio)
- Debugging "PrebuiltUI loads but Connect never resolves"
- The RTVI data channel hangs after ICE completes
- Adding a new TTS / STT / LLM service into the pipeline
- Porting this agent to a different machine

## Critical Pitfalls (read first)

### 1. Package trap: `pipecat-ai-small-webrtc-prebuilt` is NOT the real PrebuiltUI

Despite the name suggesting otherwise, `pipecat-ai-small-webrtc-prebuilt`
ships a **broken** JS bundle (Daily-flavoured glue, no `startBot` flow,
no `/api/offer` calls). The browser UI renders but hangs in "loading"
forever. The real PrebuiltUI client lives in **`pipecat-ai-prebuilt==1.0.3`**:

```bash
uv pip install pipecat-ai-prebuilt>=1.0.3
# If the broken package is present:
uv pip uninstall pipecat-ai-small-webrtc-prebuilt
```

```python
from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI
app.mount("/client", PipecatPrebuiltUI)
```

Full details + verification transcript: `references/prebuiltui-package-and-start-endpoint.md`.

### 2. PrebuiltUI requires `POST /start` — `/api/offer` alone is not enough

The browser client flow is: `startBot → POST /start → pc_id → POST /api/offer with SDP`. Implement both, or the page hangs in loading. Minimal `/start`:

```python
@app.post("/start")
async def start_bot():
    return {"pc_id": str(uuid.uuid4())}
```

### 3. `SmallWebRTCTransportParams` does NOT exist in 1.4.0

The correct params class is `pipecat.transports.base_transport.TransportParams`.
The wrong import passes lint at write time, the server starts, SDP
exchange returns 200, but the pipeline crashes with
`ImportError: cannot import name 'SmallWebRTCTransportParams'` on the
first real connection. **Treat LSP `unknown import` as a real bug, not
lint noise.** See `references/small-webrtc-verified-api-2026-06-30.md`
for the full verified API.

### 4. Mount order: API routes first, static last

```python
@app.post("/api/offer")           # 1. API first
async def offer(...): ...

@app.get("/")                     # 2. / → /client/
async def root(): return RedirectResponse("/client/")

app.mount("/client", PipecatPrebuiltUI)   # 3. mount LAST
```

Mounting StaticFiles at `/` eats `/api/offer` → 405.

### 5. Don't ship then ask the user to test

When this skill's templates or your own server reach a "looks good,
ships" state, **do not stop there**. Run the aiortc e2e probe
yourself (`templates/aiortc_e2e_probe.py`), then leave the server
running in the background for the user to point a browser at. The
user has explicitly stated they want results, not a test queue.

### 6. EdgeTTS outputs 24kHz MP3 — transport sample rate must match

The `edge-tts` library (the `EdgeTTSService` backend) outputs
**audio-24khz-48kbitrate-mono-mp3** — 24 kHz MP3 (source:
`edge_tts/constants.py:41-45`). This is NOT 16 kHz like many other TTS
services.

**Do NOT remove `audio_out_sample_rate` from TransportParams** thinking
"let the default handle it." The default is 16 kHz (from SDP
negotiation), and 24→16 non-integer resampling via default SRC produces
garbled/broken audio. Instead, **match the output rate**:

```python
params=TransportParams(
    audio_in_enabled=True,
    audio_out_enabled=True,
    audio_out_sample_rate=24000,  # ← match EdgeTTS 24 kHz output
)
```

Or, add an explicit `AudioResampleProcessor(24000, 48000)` in the
pipeline after TTS for high-quality resampling to Opus (48 kHz WebRTC).

### 7. VAD is required for STTService to trigger

If you use a custom STT service (like our `WhisperSTTService`) or any
`STTService` subclass, the pipeline **must** have Silero VAD configured
on the `LLMUserAggregatorParams`. Without it, `run_stt()` never fires,
and no transcription text appears. Confirmed from **two official
examples**:

- `pipecat-examples/simple-chatbot/server/bot-openai.py:145`
- `small-webrtc-prebuilt/test/bot.py` (line 27, 36)

```python
from pipecat.audio.vad.silero import SileroVADAnalyzer

user_agg, assistant_agg = LLMContextAggregatorPair(
    context,
    user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
    ),
)
```

### 8. `TranscriptionFrame` / `InterimTranscriptionFrame` need `timestamp`+`user_id` in 1.4.0

Pipecat 1.4.0 changed the constructor signature of these frame types.
Simply passing `(text, confidence)` raises:
```
InterimTranscriptionFrame.__init__() missing 1 required positional argument: 'timestamp'
```

Correct invocation:
```python
yield InterimTranscriptionFrame(
    text=seg.text,
    user_id="user",
    timestamp=str(time.time()),
)
```

This affects any custom STT service (WhisperSTTService, offline models,
etc.). The timestamp is a string, not a float or datetime.

### 9. `/sessions/{id}/api/offer` route is mandatory — not just `/api/offer`

The client-side `SmallWebRTCTransport` (from the JS bundle) posts the
SDP offer to `/sessions/{sessionId}/api/offer`, not bare `/api/offer`.
If only the bare route is registered, the client gets a 404 and
disconnects after "authenticating" state. Your server MUST register
both:

```python
@app.post("/api/offer")
async def offer(request, background_tasks):
    return await handle_offer(request, background_tasks)

@app.post("/sessions/{session_id}/api/offer")
async def offer_session(request, background_tasks):
    return await handle_offer(request, background_tasks)
```

Same pattern for `PATCH /api/offer` (ICE candidates).

### 10. `on_connection` must use `background_tasks.add_task()` — never `await`

The `on_connection` callback passed to `handle_web_request()` runs
inside the HTTP request handler. If you `await runner.run()` directly,
the HTTP response never returns (the client sees a hanging POST). The
pipeline runner must be **deferred** to a background task:

```python
async def on_connection(connection):
    # ❌ WRONG — blocks HTTP response forever:
    # await _run_pipeline(connection, session_id)

    # ✅ CORRECT — HTTP returns immediately, pipeline runs in background:
    background_tasks.add_task(_run_pipeline, connection, session_id)
```

### 11. `build_pipeline()` uses keyword-only args — positional call crashes

If `build_pipeline` uses a `*, transport: BaseTransport, ...` signature,
calling `build_pipeline(transport)` (positional) raises:
```
build_pipeline() takes 0 positional arguments but 1 was given
```

Always pass keyword: `build_pipeline(transport=transport)`.

### 13. Custom FrameProcessor: `_check_started` checks `self.__started` (name-mangled), NOT `self._started`

When subclassing `FrameProcessor`, the internal `_check_started` method
checks `self.__started` (Python name-mangling → `_FrameProcessor__started`).
Setting `self._started = True` has **no effect**:

```python
# ❌ WRONG — creates a new attribute, doesn't bypass the guard:
self._started = True

# ✅ CORRECT — sets the actual name-mangled attribute:
self._FrameProcessor__started = True
```

This matters when your processor is in the middle of the pipeline and
audio frames arrive before `StartFrame` has fully propagated. One
reliable fix: set it in `__init__` after `super().__init__()`:

```python
class MyProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._FrameProcessor__started = True
```

Where to find the source: `frame_processor.py` (compiled .so in 1.4.0,
logic at line ~836):
```python
def _check_started(self, frame):
    if not self.__started:
        logger.error(f"{self} Trying to process {frame} but StartFrame not received yet")
    return self.__started
```

### 14. TTSTextFrame is consumed by TTSService — it does NOT flow downstream

This is the single most counter-intuitive architectural detail in
Pipecat's pipeline. The `TTSService.process_frame` receives a
`TTSTextFrame` and **consumes it internally** — it calls
`run_tts(text)` which produces `TTSStartedFrame`, `TTSAudioRawFrame`,
and `TTSStoppedFrame`. The original `TTSTextFrame` is **not** passed
further downstream.

**Implication**: Placing a `FrameProcessor` between the TTS and the
transport output will NOT see any `TTSTextFrame`. It only sees audio
frames. To intercept the bot's text, you must choose one of these
approaches:

**Option A: Place the interceptor BEFORE TTS**

```python
processors = [
    llm,
    BotTextProcessor(),      # catches TTSTextFrame BEFORE TTS consumes it
    tts,
    transport.output(),
    assistant_agg,
]
```

**Option B: Yield `OutputTransportMessageFrame` from `run_tts`**

In a custom `TTSService.run_tts()`, yield an extra frame alongside
the audio:

```python
yield TTSStartedFrame()

# Push text to PrebuiltUI data channel
yield OutputTransportMessageFrame(
    message=json.dumps({
        "type": "bot-transcription",
        "data": {"text": text, "user_id": "assistant"},
    })
)

# Then normal audio frames
yield TTSAudioRawFrame(...)
yield TTSStoppedFrame()
```

This works because the base `TTSService._stream_audio_frames_from_iterator`
calls `push_frame()` for every yielded frame, sending the message
downstream through the transport output.

### 15. PrebuiltUI caches session IDs in browser storage

The PrebuiltUI client (`@pipecat-ai/client-react`) stores session
IDs in browser storage. After a server restart, the old session IDs
cause `PATCH /sessions/{old-id}/api/offer → 404` and the connection
hangs in `INITIALIZED`.

**Fix**: Before connecting after a server restart, clear browser storage:
```javascript
localStorage.clear();
sessionStorage.clear();
caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k))));
```

Or open a new browser tab/incognito window for a fresh session.

### 16. `WorkerRunner.run(auto_end=False)` for persistent pipelines

`PipelineWorker.run()` by default ends when the pipeline drains. For a
voice agent that needs to stay alive, use `WorkerRunner` with
`auto_end=False`:

```python
from pipecat.workers.runner import WorkerRunner

runner = WorkerRunner()
await runner.add_workers(worker)
await runner.run(auto_end=False)  # stays alive until cancelled
```

This is the pattern used in the official `simple-chatbot` example.
Without `auto_end=False`, the pipeline shuts down after the first
turn completes.

### 17. LiteLLM model selection via .env

The model used by `OpenAILLMService` is determined by three env vars
in order of priority (first non-empty wins):

1. `build_pipeline(llm_model=...)` parameter
2. `LLM_MODEL` env var
3. Hardcoded default in the function signature

For Headroom/LiteLLM proxy integration:

```env
LLM_BASE_URL=http://serverhome/litellm/headroom/v1/
LLM_API_KEY=<your-key>
LLM_MODEL=deepseek-v4-flash     # or minimax, gpt-4o, etc.
```

The process's own `LITELLM_*` env vars (LITELLM_BASE_URL,
LITELLM_API_KEY) are used by the LiteLLM proxy itself — they do NOT
affect the pipeline's `OpenAILLMService`.

### 12. Debug WebSocket "no reply" with instrumentation, not guesswork

When the browser shows "等回复..." but no audio arrives, do NOT:

- Blame sandboxes, cache, or permissions without evidence
- Claim it works because curl/Python got a reply
- Double down when user says "still not working"

Instead:

1. **Add a visible version stamp to the HTML.** Users often see a cached page. A `<div>` at the top with `Pipecat v<N>` removes that uncertainty entirely.

2. **Console.log at every step.** Not just DOM text (`lg()`). Real `console.log()` at WS open, WS close, WS error, message received, send start, send progress, send done. The browser F12 Console reveals which step never fires.

3. **Message counter in the status bar.** A running count of received WebSocket messages. If the count stays 0 after `sent complete`, the AI reply never arrived at the browser.

4. **Preload Whisper model at startup.** `WhisperSTTService(...)` at module load eliminates 20-30s cold-start delay on the first connection.

5. **Add `ws.onerror` handler.** Always log WS errors. The event object is opaque but catches edge cases like connection timeouts.

6. **Call `ac.resume()` on every AudioContext.** Browser autoplay policy suspends AudioContext created outside a user gesture. Call `ac.resume()` both at creation and in `ws.onmessage` handlers.

When the user says "no change" after you made a fix, go back to step 1 (add more instrumentation) — not to a different diagnosis.

### 18. DeepSeek/LiteLLM/Headroom: content arrives as `reasoning_content`, not `content`

When using `deepseek-v4-flash` through LiteLLM/Headroom, streaming
chunks arrive in two phases:
```json
// Phase 1 — thinking (reasoning_content)
{"delta":{"reasoning_content":"用户的意图是...","role":"assistant"}}
// Phase 2 — answer (content)
{"delta":{"content":"你好！我是你的语音助手..."}}
```
Pipecat's `BaseOpenAILLMService._process_context` only reads
`delta.content`. Phase 1 chunks have `content=None` → the LLM
never emits LLMTextFrame for thinking → thinking is silent (but
the answer in phase 2 still reaches TTS via the standard
`elif delta.content` branch).

**Fix — subclass, don't patch** (the official pattern from `NvidiaLLMService`):

```python
# src/services/llm.py
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.frames.frames import LLMThoughtStartFrame, LLMThoughtTextFrame, LLMThoughtEndFrame


class HeadroomLLMService(OpenAILLMService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._has_reasoning = False

    async def get_chat_completions(self, context):
        stream = await super().get_chat_completions(context)
        return self._handle_reasoning(stream)

    async def _handle_reasoning(self, stream):
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    # Read both naming conventions (DeepSeek vs Qwen)
                    rc = getattr(delta, "reasoning_content", None) or \
                         getattr(delta, "reasoning", None)
                    if rc:
                        if not self._has_reasoning:
                            self._has_reasoning = True
                            await self.push_frame(LLMThoughtStartFrame())
                        await self.push_frame(LLMThoughtTextFrame(text=rc))
                    elif self._has_reasoning and delta.content:
                        await self.push_frame(LLMThoughtEndFrame())
                        self._has_reasoning = False
                yield chunk
        finally:
            if self._has_reasoning:
                await self.push_frame(LLMThoughtEndFrame())
                self._has_reasoning = False
```

Then in `build_pipeline`:
```python
from src.services.llm import HeadroomLLMService
llm = HeadroomLLMService(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
    settings=OpenAILLMService.Settings(model=LLM_MODEL),
)
```

**Why subclass instead of patching `base_llm.py` and `chat_completion_chunk.py`:**
- Patching pipecat source code gets overwritten on every `pip install -U`.
- `NvidiaLLMService` uses the exact same pattern (`pipecat/services/nvidia/llm.py:248-326`).
- Subclassing means the reasoning behavior is local, testable, and upgrade-safe.
- `LLMThoughtStartFrame` / `LLMThoughtTextFrame` / `LLMThoughtEndFrame` are official
  frames — `RTVIObserver` already routes them to the browser as `bot-llm-text` events.

**Reference implementation**: `pipecat/services/nvidia/llm.py:248-326` is the
official reference — read it before writing your subclass. The official version
also calls `_close_inner_stream(stream)` in the `finally` block to release the
OpenAI SDK's network sockets; for short-lived voice sessions this is rarely an
issue but matters under heavy concurrent load.

**Disabling thinking mode via `thinking: {"type": "disabled"}` does NOT work**
through the LiteLLM/Headroom proxy — the proxy strips the parameter and the
model continues to emit `reasoning_content`. The subclass fix is mandatory for
DeepSeek models through this proxy.
chat from context` + `completion tokens: X` but zero frames reach
EdgeTTS. Verify with BotTextProcessor logging.

### 19. BotTextProcessor may not see LLMTextFrame despite `_push_llm_text` being called

Even with the `reasoning_content` fix applied, `_push_llm_text` pushes
`LLMTextFrame` via `self.push_frame(LLMTextFrame(text))`, which queues
the frame in the **next processor's async input queue**
(`__input_queue`). Due to async scheduling, the queued frame may never
reach the processor's `process_frame` — only `MetricsFrame` (pushed via
a different path after `_process_context` completes) appears in logs.

**Diagnosis** — add a verbose BotTextProcessor that logs all frames:
```python
async def process_frame(self, frame: Frame, direction: FrameDirection):
    from loguru import logger
    name = type(frame).__name__
    if name != 'InputAudioRawFrame':
        logger.info(f"[BotText] {name}")
    # ... regular processing ...
```

If `[BotText] MetricsFrame` appears but `[BotText] LLMTextFrame` /
`[BotText] LLMFullResponseStartFrame` never does, the LLM output frames
are stuck in the async queue.

**Workaround**: enable `enable_direct_mode=True` on the downstream
processor so frames are processed synchronously instead of queued:
```python
class BotTextProcessor(FrameProcessor):
    def __init__(self):
        super().__init__(enable_direct_mode=True)  # ← synchronous processing
        ...
```

### 20. Test audio injection for WebRTC pipeline verification

The CDP browser (headless Chrome) cannot access `getUserMedia()` for
real microphone input, and SmallWebRTC WebRTC connections also fail
in headless mode. To test the pipeline WITHOUT a browser:

**Option A: Server-side audio injection via HTTP**

Add a `/inject_test_audio` endpoint to your FastAPI app:
```python
from pipecat.frames.frames import InputAudioRawFrame

_active_inbounds: dict[str, object] = {}  # session_id → transport.input()

# In _run_pipeline:
_active_inbounds[session_id] = transport.input()

@app.post("/inject_test_audio/{session_id}")
async def inject_test_audio(session_id: str):
    inbound = _active_inbounds.get(session_id)
    data = _get_test_audio()  # 16kHz 16-bit mono PCM bytes
    frame = InputAudioRawFrame(audio=data, sample_rate=16000, num_channels=1)
    await inbound.push_frame(frame)
    return {"status": "ok", "bytes": len(data)}

@app.post("/inject_test_audio")
async def inject_test_audio_latest():
    # inject into the most recently created session
    ...
```

**Option B: aiortc headless probe**

Use the existing `templates/aiortc_e2e_probe.py` template — this
establishes a real WebRTC connection without a browser and verifies
the full pipeline (SDP → ICE → audio frames). The probe is
deterministic and agent-runnable.

### 21. CDP browser cannot test WebRTC — never rely on it

The CDP browser (used by Hermes `browser_navigate` / `browser_click`)  
is headless Chrome and **cannot establish WebRTC PeerConnections**:
- `getUserMedia()` fails even with `enableMic: false` flag
- ICE candidates are not exchanged
- The JS client's `startBotAndConnect()` hangs at `authenticating`
- `POST /start` succeeds but `/api/offer` never arrives

**Do NOT waste cycles trying to automate WebRTC in CDP**. Serve the
JS client, leave the server running, and tell the user where to point
their real browser. The Verifiable test path is:
1. Server logs show pipeline start (`pipeline starting` log line)
2. AIORTC probe passes (see template)
3. `curl /inject_test_audio` shows BotTextProcessor logs the injection
4. User opens real browser with mic → confirms audio

### 22. Whisper STT must use `SegmentedSTTService`, not base `STTService`

A custom Whisper STT that extends base `STTService` transcribes each
20ms audio frame (640B @ 16kHz) **independently** — Whisper cannot
recognize speech from 20ms chunks, so no transcription is produced.

**Correct base class is `SegmentedSTTService`**, which accumulates
audio in `_audio_buffer` and calls `run_stt(full_buffer)` when VAD
signals user stopped speaking:

```python
from pipecat.services.stt_service import SegmentedSTTService

class WhisperSTTService(SegmentedSTTService):  # ← not STTService
```

**VAD signals flow UPSTREAM** to reach the STT. The `LLMUserAggregator`
(containing the `SileroVADAnalyzer`) pushes `VADUserStoppedSpeakingFrame`
via `_queued_broadcast_frame` with `FrameDirection.UPSTREAM`:

```python
# llm_response_universal.py
def _queued_broadcast_frame(self, frame_cls, **kwargs):
    await self.queue_frame(frame_cls(**kwargs))
    await self.push_frame(frame_cls(**kwargs), FrameDirection.UPSTREAM)
```

`SegmentedSTTService._handle_user_stopped_speaking` then writes the
buffer to a WAV and calls `run_stt(content.read())` with the full
accumulated audio — confirmed from `stt_service.py` L778-793.

**Verify with**: add a log at `run_stt` entry:
```python
logger.info(f"[WHISPER-STT] run_stt called with {len(audio)}B audio")
```
- 640B → wrong base class (STTService, no accumulation)
- 20KB+ → correct (SegmentedSTTService with accumulation)

### 23. Inject test audio via `_audio_in_queue`, not `push_frame`, to bypass pipeline backlog

When the pipeline is busy (e.g. LLM is streaming a greeting response),
`inbound.push_frame(InputAudioRawFrame(...))` queues the frame to
the **next processor's async queue** (`self._next.queue_frame()`).
The frame stays there until the processor finishes its current work —
by which time VAD may have timed out or the audio buffer was discarded.

**Fix**: push frames directly to the input transport's `_audio_in_queue`
instead, which the dedicated `_audio_task_handler` picks up on its own
schedule, independent of the pipeline's processing backlog:

```python
inbound = _active_inbounds[session_id]  # transport.input()
data = _get_test_audio()  # 16kHz 16-bit mono PCM bytes
chunk_size = 640  # 20ms per frame

if hasattr(inbound, '_audio_in_queue') and inbound._audio_in_queue:
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        frame = InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
        await inbound._audio_in_queue.put(frame)
else:
    # fallback: direct push_frame (queued behind pipeline backlog)
    ...
```

The `_audio_in_queue` is created in `BaseInputTransport._create_audio_task()`
which runs when `StartFrame` reaches the transport and `audio_in_enabled=True`.

### 24. Event queue + HTTP polling for browser Events panel (not data channel)

Sending debug events via `OutputTransportMessageFrame` through the
WebRTC data channel has two problems: the aiortc probe has no data
channel, and unknown message types are dropped by the JS client.

**Better approach**: in-memory event queue + `GET /events` + polling:

**Server (pipeline.py)**:
```python
_event_queue: list[str] = []

def push_event(msg: str):
    _event_queue.append(msg)
    if len(_event_queue) > 200:
        _event_queue[:50] = []

def pop_events() -> list[str]:
    items = list(_event_queue)
    _event_queue.clear()
    return items
```

**Server (FastAPI)**:
```python
@app.get("/events")
async def get_events():
    return {"events": pop_events()}
```

**Client (app.js)**:
```javascript
const baseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
setInterval(async () => {
    const resp = await fetch(`${baseUrl}/events`);
    const data = await resp.json();
    for (const ev of data.events || [])
        client.addEvent('server', ev);
}, 1000);
```

### 25. JS client auto-connect + global test function for CDP

`DOMContentLoaded` may fire before the module is fully loaded in CDP.
Run auto-connect in `setTimeout(500)` and expose a global inject function:

```javascript
window.addEventListener('DOMContentLoaded', () => {
  const client = new VoiceChatClient();
  window.__voiceClient = client;
  window.__injectTestAudio = async () => {
    const baseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
    const resp = await fetch(`${baseUrl}/inject_test_audio`, { method: 'POST' });
    const result = await resp.json();
    client.addEvent('test-audio', `Sent ${result.bytes}B test audio`);
    return result;
  };
  setTimeout(async () => {
    client.transportType = 'smallwebrtc';
    client.transportSelect.value = 'smallwebrtc';
    await client.connect();
  }, 500);
});
```

Then invoke from CDP: `await window.__injectTestAudio()`.

### 26. Vite dev server must listen on `0.0.0.0` for cross-machine access

Default `npm run dev` binds only to `localhost` / `[::1]`, unreachable
from other machines on the network/Tailscale. Create `vite.config.js`:

```javascript
import { defineConfig } from 'vite';
export default defineConfig({
  server: { host: '0.0.0.0', port: 5173 },
});
```

Or start with `npx vite --host 0.0.0.0`. Verify with `ss -tlnp | grep 5173`:
`[::1]:5173` = localhost-only, `0.0.0.0:5173` = reachable.

## References

| File | When to read |
| --- | --- |
| `references/architecture.md` | Pipeline structure, service composition, LLM/STT/TTS wiring |
| `references/small-webrtc-prebuilt-2.5.0.md` | Snapshot of the broken package's layout (kept for reference; do not use) |
| `references/small-webrtc-verified-api-2026-06-30.md` | Verified transport API + the `SmallWebRTCTransportParams` trap |
| `references/prebuiltui-package-and-start-endpoint.md` | The real PrebuiltUI package, the `/start` endpoint, and the aiortc verification recipe |
| `references/official-example-comparison-2026-06-30.md` | Module-by-module comparison vs pipecat-examples simple-chatbot and small-webrtc-prebuilt — VAD, EdgeTTS 24kHz, TranscriptionFrame, routes, pipeline order |
| `references/whisper-bottext-restart-2026-06-30.md` | Whisper timestamp fix, BotTextProcessor (OUTDATED placement — superseded by pitfall 13/14), restart script, background log reading |
| `references/bot-text-processor-lifecycle-2026-06-30.md` | BotTextProcessor lifecycle debugging: `__started` name-mangling fix, TTSTextFrame consumption by TTS, push_frame ordering, alternative yield-from-TTS approach |
| `references/websocket-rawpcm-2026-06-29.md` | RawPCM WebSocket path — JS test page, AudioContext notes, `auto_end=False` |
| `references/deployment-2026-06-29.md` | Tailscale + TURN/STUN environment notes |
| `references/deepseek-reasoning-content-fix-2026-06-30.md` | DeepSeek reasoning_content patch: ChoiceDelta model, base_llm.py elif, curl verification |
| `references/stt-accumulation-skip-tts-2026-06-30.md` | STT audio accumulation (SegmentedSTTService), VAD upstream signal flow, skip_tts field(init=False) caveat |

## Templates

| File | Purpose |
| --- | --- |
| `templates/server_prebuilt.py` | Known-good PrebuiltUI + SmallWebRTC server. Drop into `src/server_prebuilt.py` |
| `templates/aiortc_e2e_probe.py` | Headless verification probe — runs the full client flow without a browser. Use this every time you think the server is ready |

## Verification workflow

Before declaring the PrebuiltUI path "done":

1. Start the server: `uv run --project . python -m src.server_prebuilt &`
2. Run the probe: `uv run --project . python templates/aiortc_e2e_probe.py`
3. Expect all 7 numbered steps to print without `[FAIL]`
4. Leave the server running; tell the user the URL

If the probe passes, the browser will work too. The probe catches:
- Missing `/start` or `/api/offer` endpoints (returns 404/405)
- Wrong params class (ImportError on first connection)
- Mount-order bugs (SDP/ICE succeeds but the worker never starts)
- Pipeline crashes that only surface after 8s of WebRTC traffic

## Quick links

- Pipecat 1.4.0 source of truth: `~/.cache/uv/archive-v0/AI_OIBdJViefoUKm/pipecat/`
- venv site-packages: `~/workspace/pipecat/.venv/lib/python3.11/site-packages/`
- LLM endpoint (Headroom via serverhome): `http://serverhome.tail2e6efb.ts.net/litellm/headroom/v1/`
- RawPCM path (in this repo): `src/bot.py` (port 8765, WebSocket)
- PrebuiltUI path (in this repo): `src/server_prebuilt.py` (port 8766, WebRTC)
