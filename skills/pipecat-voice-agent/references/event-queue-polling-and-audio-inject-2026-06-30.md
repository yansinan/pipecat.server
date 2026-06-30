# Event Queue Polling and Audio Injection Patterns

Session: 2026-06-30
Context: Debugging pipecat voice agent with CDP browser, aiortc, and helix (remote machine)

## Problem: Browser Events panel shows no Chinese-marker debug events

Root cause: Server sends events via `OutputTransportMessageFrame` through WebRTC data
channel, but the JS client's `handleMessage()` switch only processes RTVI-defined types
(`BOT_READY`, `ERROR`, `BOT_TRANSCRIPTION`, etc.). Custom `app-message` types are silently
dropped — no default case in the switch.

Fix: Replace data-channel delivery with an in-memory event queue + HTTP polling:

### Server-side (pipeline.py)
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

### Server-side (bot_js_client.py)
```python
@app.get("/events")
async def get_events():
    from src.pipeline import pop_events
    return {"events": pop_events()}
```

### Client-side (app.js)
```javascript
const baseUrl = (import.meta.env.VITE_BOT_START_URL || 'http://localhost:7860/start').replace('/start', '');
setInterval(async () => {
    const resp = await fetch(`${baseUrl}/events`);
    const data = await resp.json();
    for (const ev of data.events || [])
        client.addEvent('server', ev);
}, 1000);
```

## Problem: CDP browser auto-connect doesn't work

Root cause: `DOMContentLoaded` fires before Vite finishes compiling the
JS module. The event listener never fires, so `new VoiceChatClient()`
is never called, element IDs are in HTML but not queryable via
`getElementById()`.

Fix: No fix needed — the `setTimeout(500)` approach works. But for CDP
testing, **never rely on UI clicks** (`browser_click` on the Connect
button). Instead expose `window.__injectTestAudio()` and call it via
`browser_console`.

## Problem: Test audio injection doesn't trigger VAD/STT

Root cause: `inbound.push_frame(InputAudioRawFrame(82KB))` queues the
single large frame to the next processor's (`RTVIProcessor`) async
queue. The queue is backlogged by the LLM's greeting response. By
the time the frame reaches STT, VAD has already timed out.

Fix: Push directly to `_audio_in_queue` in 640B chunks (matching
real WebRTC audio frame size):

```python
if hasattr(inbound, '_audio_in_queue') and inbound._audio_in_queue:
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        frame = InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1)
        await inbound._audio_in_queue.put(frame)
```

The `_audio_task_handler` picks frames from this queue independently
of the pipeline's processing backlog.

## Problem: Vite dev server unreachable from remote machine (helix)

Root cause: `npm run dev` binds only to `[::1]:5173` (localhost).
From helix (`100.66.66.102`), `http://100.66.66.249:5173/` returns
connection refused.

Fix: Create `vite.config.js` with `host: '0.0.0.0'`. Verify with:
```bash
ss -tlnp | grep 5173
# Expect: 0.0.0.0:5173  — not  [::1]:5173
curl -s -o /dev/null -w "%{http_code}" http://100.66.66.249:5173/
# Expect: 200
```
