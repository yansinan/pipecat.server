# Pipeline shutdown vs LLM HTTP streaming race

## Symptom

- LLM runs (server log shows `[DEEPSEEK-REASONING]` + `[LLM-CONTENT]` chunks)
- LLM reports completion tokens (`processing time: X.XXXs`)
- BotTextProcessor logs only `MetricsFrame` — **no** `LLMTextFrame`,
  `LLMFullResponseStartFrame`, or `LLMFullResponseEndFrame`
- No TTS output, no client audio

## Root cause

WebRTC connection lifecycle:

1. Client's audio track ends → ICE state transitions to `closed`
2. `SmallWebRTCTransport.on_closed()` fires → runner receives EndFrame/CancelFrame
3. Pipeline worker shuts down → **all queue processors stop**
4. BotTextProcessor's queue handler exits — will not process any more frames

But at step 3, the LLM's streaming HTTP request (initiated by `_process_context
→ get_chat_completions`) is **still running asynchronously**. The LLM service:

- Continues iterating over `async for chunk in chunk_iter` (HTTP response still
  arriving)
- Calls `_push_llm_text(text)` for each `reasoning_content` or `content` chunk
- `push_frame(LLMTextFrame(...))` → `_next.queue_frame(frame)` puts the frame
  into BotTextProcessor's **input queue**
- But BotTextProcessor's queue handler **has already stopped** — the frame
  sits in the queue forever

The LLM finishes normally (since HTTP is independent of WebRTC), but its output
frames are orphaned.

## Timing diagram

```
WebRTC connected:   |-----------------------|
Audio stream ends:                          |---PIPELINE SHUTDOWN--->
LLM HTTP request:   |-------------------LLM streaming-------------------|
LLMTextFrame push:                          |◄── orphaned in queue ──►|
                                                BotText never dequeues
```

## Mitigations

### 1. `enable_direct_mode=True` on BotTextProcessor

Direct mode processes `process_frame()` synchronously — no queue involved.
The frame goes downstream immediately rather than waiting for the queue
handler to pick it up.

```python
class BotTextProcessor(FrameProcessor):
    def __init__(self):
        super().__init__(enable_direct_mode=True)   # ← ADD THIS
        self._FrameProcessor__started = True

    async def process_frame(self, frame, direction):
        # frames processed immediately, no queue
        ...
```

Trade-off: direct mode runs in the caller's task context, which means it
blocks the caller until `process_frame` returns. For lightweight processors
(BotText just pushes frames onward), this is fine.

### 2. Keep pipeline alive until LLM completes

In the server's `on_closed` handler, instead of immediately stopping the
runner, wait for pending LLM work to finish:

```python
async def on_closed(self):
    # Signal pipeline to stop but don't cancel pending LLM HTTP requests
    await self.push_frame(EndFrame())
    # Wait for the pipeline to drain
    await asyncio.sleep(5)  # crude — better to track pending work
    await self._runner.stop()
```

A more precise approach: the LLM service exposes an `_process_context` that
you could track via a future or counter.

### 3. Fast pipeline shutdown + audio timeout

If the user stops speaking and no more audio arrives within the VAD stop
timeout (default 2s), the turn completes naturally and the LLM runs BEFORE
the connection would close. The race only happens when:

- The audio source stops (client disconnects) **during** LLM inference
- The pipeline shuts down before the LLM HTTP response completes

For `MediaPlayer(loop=True)` in the test client, the track still ends
because aiortc's MediaPlayer sends a finite stream even with loop mode —
the loop feature only works for `MediaRecorder`, not live tracks. Use a
custom `AudioStreamTrack` with infinite loop instead.

## Detection

Add a `DebugFrameProcessor` between LLM and BotText:

```python
DebugFrameProcessor(name="after-llm"),
BotTextProcessor(),
```

If `[Frame:after-llm]` shows `LLMTextFrame` entries but `[BotText]` shows
only `MetricsFrame`, you have hit this race.

Also check the server log for:
```
[WHISPER-STT] run_stt called with NNNNB audio
...
OpenAILLMService#N: Generating chat from context [...]
[LLM-CONTENT] text='...'
[BotText] MetricsFrame          ← NO LLMTextFrame here
```

If `[LLM-CONTENT]` lines appear before `MetricsFrame` but no `[BotText]
LLMTextFrame` follows, the pipeline shut down during LLM streaming.

## Alternate reference

- `stt-accumulation-segmented-stt-service.md` for the companion issue (STT
  not accumulating audio)
- `deepseek-litellm-reasoning-content.md` for the reasoning_content patch
