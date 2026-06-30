# FastAPI WebSocket + RawPCMSerializer transport path

## Architecture

```
Browser mic → AudioWorklet → Int16 PCM (320 bytes / 20ms)
    ↓ WebSocket binary
FastAPIWebsocketTransport
    ↓ RawPCMSerializer.deserialize()
InputAudioRawFrame (16000Hz, mono, 16bit PCM)
    ↓ push_audio_frame() → _audio_in_queue → _audio_task_handler
    ↓ Pipeline
WhisperSTTService → Transcribes to text
    ↓ + audio passthrough (STTService default: audio_passthrough=True)
LLMUserAggregator (with SileroVADAnalyzer)
    ↓ VAD detects speech-end → commits to LLM context → queues LLMRunFrame
OpenAILLMService (Headroom/LiteLLM → minimax)
    ↓
EdgeTTSService → OutputAudioRawFrame (PCM)
    ↓
WebSocket → Browser AudioContext → Speaker
```

## Critical params

```python
transport = FastAPIWebsocketTransport(
    websocket=ws,
    params=FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_in_sample_rate=16000,      # MUST match serializer! VAD needs this
        audio_out_enabled=True,
        serializer=RawPCMSerializer(),
    ),
)
```

## `WorkerRunner.run(auto_end=False)` —#1 cause of "bot says hello then goes silent"

**Default `auto_end=True`**: WorkerRunner exits after the greeting pipeline finishes.
WebSocket closes. User's speech never reaches the server.

**Fix**: `await runner.run(auto_end=False)` keeps the runner alive.

```python
runner = WorkerRunner()
await runner.add_workers(worker)
await worker.queue_frames([LLMRunFrame()])
try:
    await runner.run(auto_end=False)   # ← THIS
except WebSocketDisconnect:
    logger.info("Client disconnected")
finally:
    await worker.cancel()
```

## Test page with standalone "发送测试语音" button

Serves pre-generated PCM audio via `/test.pcm`. Button creates its own WebSocket
(no mic needed) and sends the audio to the server. Pattern:

1. Generate PCM file server-side at startup (`struct.pack('<h', ...)`)
2. Serve via `FileResponse` at `/test.pcm`
3. Browser `fetch('/test.pcm')` → `arrayBuffer` → `ws.send(slice)` in 320-byte chunks
4. Button creates `AudioContext` + `WebSocket` on its own (no `getUserMedia` needed)
5. Waits for greeting, then sends test audio

## Browser AudioContext rule

**ONE AudioContext, created in user gesture.** Broswer blocks AudioContext
created in async callbacks (`ws.onmessage`, `setTimeout`). Pattern:

```javascript
// ✅ Correct: created in button click handler
btn.onclick = async () => {
    ac = new AudioContext({sampleRate: 16000});
    ws = new WebSocket(...);
};

// ❌ Wrong: created in onmessage callback
ws.onmessage = () => {
    ac = new AudioContext({sampleRate: 16000});   // Browser mutes this
};
```

## Cache directory layout

```
.venv/     1.1G   Python virtual environment (keep)
cache/     479M   Whisper models + Silero VAD + NLTK (keep)
  ├── whisper/    faster-whisper model files
  ├── silero/     VAD ONNX model
  └── nltk/       tokenizer data
.archive/   40K   Outdated plans, backups, self-written pages
```

Delete `~/.cache/` or `./.cache/` (uv download cache — 1.1G, regnerable).
Delete `nltk_data/` after merging into `cache/nltk/`.

## Client-side test script (Python, for CI/verification)

```python
import asyncio, websockets
async def test():
    ws = await websockets.connect('ws://localhost:8765/conversation')
    g = await asyncio.wait_for(ws.recv(), timeout=12)  # greeting
    with open('/path/to/test.pcm', 'rb') as f: audio = f.read()
    for i in range(0, len(audio), 320):
        await ws.send(audio[i:i+320])
        await asyncio.sleep(0.01)
    r = await asyncio.wait_for(ws.recv(), timeout=25)   # response
    print(f"OK: greeting={len(g)}b send={len(audio)}b resp={len(r)}b")
    await ws.close()
asyncio.run(test())
```

## Known pitfalls

- Missing `auto_end=False`: bot says hello, WS closes, user speech lost
- Missing `audio_in_sample_rate=16000`: VAD doesn't trigger on correct sample rate
- `WorkerRunner` blocks the `conversation()` handler — each WS connection gets its own
  runner. Fine for single-client, but concurrent clients each spawn their own pipeline.
- LLM greeting is triggered by `worker.queue_frames([LLMRunFrame()])` before
  `runner.run()`. The greeting plays immediately on connection.
- Subsequent LLM calls are triggered by VAD: Silero detects speech-end → aggregator
  commits text → queues LLMRunFrame internally. No explicit `queue_frames` needed
  after the initial greeting.
