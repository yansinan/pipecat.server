# Test Audio Module Isolation Pattern

**Sessions**: 2026-06-30 + 2026-06-30 — evolved across multiple iterations.

## Problem

Test audio code was inline in `bot_js_client.py` — hard to remove, no real TTS
audio, sine wave fallback useless for VAD, separate session registry.

## Final Solution: `src/test_audio.py`

Single `TestAudioInjector` class, no own registry — uses framework's
`_pcs_map` (from `SmallWebRTCRequestHandler`) to find active sessions.

### Public interface

```python
from src.test_audio import test_audio

# Once at startup (after webrtc_handler is created):
test_audio.set_handler(webrtc_handler)

# In _run_pipeline — stash transport.input() on connection:
connection._inject_inbound = transport.input()

# Inject endpoint — one line:
return await test_audio.inject_latest()
```

### How it works internally

```python
class TestAudioInjector:
    def set_handler(self, handler):
        # Takes a reference to handler._pcs_map — no lifecycle management
        self._pcs_map = handler._pcs_map

    async def inject_latest(self):
        # 1. Find latest connection from framework's connection pool
        keys = list(self._pcs_map.keys())
        conn = self._pcs_map[keys[-1]]
        # 2. Get the inbound stashed by _run_pipeline
        inbound = getattr(conn, "_inject_inbound", None)
        # 3. Generate TTS audio and push
        n = await self._push_to(inbound)
        # 4. Notify browser Events panel
        ...send OutputTransportMessageFrame UPSTREAM...
        return {"status": "ok", "bytes": n}

    async def _gen_tts(self):
        # ⚠️ MUST be async — called from FastAPI async endpoint
        # asyncio.run() fails with "cannot be called from running event loop"
        from edge_tts import Communicate
        ...edge-tts → ffmpeg pipe to stdout...

    async def _push_to(self, inbound):
        data = await self._gen_tts()
        # Prefer _audio_in_queue (independent of pipeline backpressure)
        if hasattr(inbound, "_audio_in_queue") and inbound._audio_in_queue:
            for i in range(0, len(data), CHUNK_SIZE):
                await inbound._audio_in_queue.put(...)
        else:
            for i in range(0, len(data), CHUNK_SIZE):
                await inbound.push_frame(...)
```

### No sine wave fallback

Old code generated a 0.3s 220Hz sine wave when edge-tts/ffmpeg was missing.
This is **useless** — VAD can't detect speech from a pure tone. The new code
just raises `FileNotFoundError` with a clear message about what to install.

**Rule**: A missing dependency should fail at the first call, not silently
produce unusable data. The error message tells the user exactly what to install
and why.

### Why the `_gen_tts` async lesson matters

Initial version used `asyncio.run(_gen())` with a `RuntimeError` catch to
switch to `loop.run_until_complete()`. This failed because:

1. `inject_latest()` is a FastAPI async endpoint → runs in an event loop
2. `asyncio.run()` from inside a running loop raises `RuntimeError`
3. The except branch tried `loop.run_until_complete()` — also fails from
   inside a coroutine

**Fix**: Make `_gen_tts` itself `async`, use `await` directly instead of
`asyncio.run()`. This is the correct pattern whenever a synchronous-looking
method actually runs async I/O (edge-tts HTTP calls, ffmpeg subprocess pipes).

### Deletion path

Remove the module:
```bash
rm src/test_audio.py
```

Remove from bot_js_client.py:
1. `from src.test_audio import test_audio`
2. `test_audio.set_handler(webrtc_handler)` 
3. `@app.post("/inject_test_audio")` endpoint
4. `connection._inject_inbound = transport.input()` in `_run_pipeline`
5. `window.__injectTestAudio` in `app.js` (or keep as no-op)
