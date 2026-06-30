# SmallWebRTC + PrebuiltUI Integration Debug Transcript

Session: 2026-06-30 → 2026-07-01, debugging chain from broken "一直 loading" to full
Client READY / Agent READY + conversation panel working end-to-end.

## Bug 1: Wrong TransportParams class

**Error**: `ImportError: cannot import name 'SmallWebRTCTransportParams' from pipecat.transports.smallwebrtc.transport`

**Root cause**: Class doesn't exist. Use `TransportParams` from `pipecat.transports.base_transport`.

**Fix**: `from pipecat.transports.base_transport import TransportParams`

## Bug 2: Wrong JS bundle in wheel

**Symptom**: PrebuiltUI loaded but Connect button produced `authenticating → disconnected` instantly. Server log: POST /start returned 200 but client never called /api/offer.

**Root cause**: `pipecat-ai-small-webrtc-prebuilt==2.5.0` contains a Daily-based client that doesn't talk to `/api/offer`.

**Fix**: `uv pip install pipecat-ai-prebuilt==1.0.3` (separate package from `pipecat-ai-small-webrtc-prebuilt`), switch import to `from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI`.

## Bug 3: /start returned wrong field name

**Symptom**: Client called /start but proceeded no further.

**Root cause**: Client expects `sessionId` (camelCase), the endpoint returned `pc_id` (snake_case).

**Also**: Client expects `iceConfig: { iceServers: [...] }` in the response to configure ICE servers.

**Fix**: Return `{"sessionId": "default", "iceConfig": {"iceServers": [...]}}`. Any non-empty
sessionId works — the client only uses it to construct the URL path. The framework
identifies connections via `pc_id`, not the sessionId you return.

## Bug 4: Client uses /sessions/{id}/api/offer path

**Symptom**: Server log showed `POST /sessions/{sessionId}/api/offer → 404 Not Found` while `POST /api/offer` was never called.

**Root cause**: SmallWebRTC transport in the client uses the sessionId from /start as a URL prefix.

**Fix**: Register `/sessions/{session_id}/api/offer` (POST for offer, PATCH for ICE). Do NOT
register the bare `/api/offer` — it's dead code from pre-1.10 clients and is never called.

## Bug 5: pipeline runner blocked HTTP handler

**Symptom**: POST /api/offer timed out (curl hung for 5+ seconds). Client saw ICE `connecting → disconnected` after 10s.

**Root cause**: `on_connection` callback did `await runner.run()` which blocks until pipeline ends. The HTTP handler never returned the SDP answer.

**Fix**: Use `background_tasks.add_task(_run_pipeline, connection)` and return immediately.

## Bug 6 (supersedes earlier note): Silent RTVI message drop with `observers=[RTVIObserver()]`

**Earlier diagnosis was wrong.** The original Bug 6 fix said "add observers=[RTVIObserver()]".
That fix worked for *connection* but silently broke *all RTVI messages*.

**Symptom** (after the original fix): pipeline runs, LLM responds, TTS speaks, but the
PrebuiltUI conversation panel never shows assistant text. No `BotOutput` events in the
log. No errors.

**Root cause**: When you pass `observers=[RTVIObserver()]`, `PipelineWorker` (worker.py:385-405)
detects the external observer and skips creating its own. But your `RTVIObserver()` was
constructed with `rtvi=None`. `send_rtvi_message()` in `observer.py:393` checks
`if self._rtvi:` → False → silently drops every message.

**Fix — let PipelineWorker auto-create the observer wired to its RTVIProcessor**:
```python
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(...),
    # don't pass observers=
)
```

Verified working with `pipecat-examples/code-helper/server/bot.py:188-198` which uses the
correct pattern (only `rtvi_observer_params=`, never `observers=[...]`).

Also: don't put `RTVIObserver` or `RTVIProcessor()` in the `processors` list.
WorkerRunner / PipelineWorker create it automatically and prepend it. Putting
`RTVIProcessor()` in the list makes the auto-creation skip path trip the
"RTVIProcessor found in pipeline but no RTVIObserver in observers" error and refuse to start.

## Bug 7: build_pipeline keyword-only args

**Symptom**: `TypeError: build_pipeline() takes 0 positional arguments but 1 was given`.

**Root cause**: Function signature is `def build_pipeline(*, transport, ...)` — keyword-only.

**Fix**: `build_pipeline(transport=transport)`.

## Bug 8: Conversation panel empty despite TTS playing — missing official LLMTextProcessor

**Symptom**: TTS works (you hear the bot speak), but PrebuiltUI's conversation panel never
shows the assistant text. No `BotOutput` log line.

**Root cause**: `RTVIObserver.on_push_frame()` (`observer.py:483-495`) has a `src` guard:
```python
elif isinstance(frame, AggregatedTextFrame) and (...):
    if not isinstance(src, BaseOutputTransport):
        mark_as_seen = False        # ← skip!
    else:
        await self._handle_aggregated_llm_text(frame)
```

When you use *separate* STT + LLM + TTS services, `AggregatedTextFrame` is produced by
`EdgeTTSService` (or your TTS) and pushed downstream. `src` is the TTS service, not
`BaseOutputTransport`. The observer marks it seen but does NOT emit BotOutput. Only the
AggregatedTextFrame that flows back upstream through `transport.output()` (where `src`
IS `BaseOutputTransport`) gets the BotOutput treatment — and in a separate-services
pipeline, that path doesn't exist.

**Fix — add the official `LLMTextProcessor`** between LLM and TTS:
```python
from pipecat.processors.aggregators.llm_text_processor import LLMTextProcessor
processors = [
    transport.input(),
    stt, user_agg,
    llm,
    LLMTextProcessor(),  # ← converts LLMTextFrame → AggregatedTextFrame
    tts,
    transport.output(),
    assistant_agg,
]
```

**Why this works**: `LLMTextProcessor` produces `AggregatedTextFrame` upstream of TTS;
it flows through `assistant_agg` → back upstream through `transport.output()`. The
observer sees it with `src = transport.output()` → emits BotOutput → PrebuiltUI
renders text in the conversation panel.

**Crucial — DO NOT write your own `BotTextProcessor`**. The custom FrameProcessor approach
fails for two reasons:
1. `self._started = True` does nothing — the field is name-mangled to
   `_FrameProcessor__started` (double-underscore). Setting `self._FrameProcessor__started = True`
   in `__init__` is the only way.
2. Even after fixing the lifecycle, pushing `OutputTransportMessageFrame` directly
   bypasses the observer and bypasses PrebuiltUI's BotOutput renderer. The conversation
   panel still won't show the text.

**Source**: `pipecat.processors.aggregators.llm_text_processor.LLMTextProcessor`
(`/home/dr/workspace/pipecat/.venv/lib/python3.11/site-packages/pipecat/processors/aggregators/llm_text_processor.py:30`).
Official `code-helper/server/bot.py:101,181` uses it.

## Bug 9: SmallWebRTC `_pending_app_messages` race in Cloud browser environment

**Symptom**: Browser shows "Client READY, Agent CONNECTED" but first/next text messages
are dropped. Server log shows:
```
Client not connected. Queuing app-message.
```
repeatedly. Messages never reach the pipeline.

**Root cause** (`connection.py:656-672`):
```python
def is_connected(self) -> bool:
    if not self._connect_invoked:
        return False
    if self._last_received_time is None:
        return self._pc.connectionState == "connected"
    return (time.time() - self._last_received_time) < 3   # ← 3-second window
```

Two compounding issues:
1. After the first ping, `is_connected()` only checks the 3-second ping window. If
   the client doesn't ping for >3s (e.g., it's busy rendering), text messages get queued.
2. aiortc's DTLS handshake in Cloud browser environments can stall — `connectionState`
   stays at "connecting" even after ICE completed. So the initial `is_connected()`
   fallback (`connectionState == "connected"`) returns False forever.

**Fix — manually flush pending messages after pipeline starts**:
```python
async def _run_pipeline(connection):
    transport = SmallWebRTCTransport(webrtc_connection=connection, params=...)
    worker, _ = build_pipeline(transport=transport, ...)
    runner = WorkerRunner()
    await runner.add_workers(worker)
    
    # Flush queued app-messages — the data channel IS open even if is_connected() says no
    pending = getattr(connection, "_pending_app_messages", [])
    if pending:
        for msg in list(pending):
            await connection._call_event_handler("app-message", msg)
        pending.clear()
    
    await runner.run(auto_end=False)
```

## Final verification

```
Browser status: Client: READY, Agent: READY
Server log: Generating chat → prompt tokens → completion tokens → Bot started speaking
PrebuiltUI conversation panel: both user and assistant bubbles visible
```

If you see all three (browser ready + LLM tokens + BotOutput in panel), the stack is
correctly wired. If the conversation panel is empty but audio works, re-check
`observers=[RTVIObserver()]` (Bug 6) and `LLMTextProcessor` (Bug 8).