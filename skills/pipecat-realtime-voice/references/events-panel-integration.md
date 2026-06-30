# Events Panel Integration — Chinese Markers for STT/LLM/TTS

## Purpose

Display real-time pipeline progress in the browser Events panel with Chinese
markers. Helps debugging without server log access.

## Architecture Evolution

### V1: Data Channel (abandoned)

`OutputTransportMessageFrame` pushed UPSTREAM → RTVIProcessor → browser data
channel. **Problem**: `@pipecat-ai/client-js` silently drops unknown message
types (only handles `bot-transcription`, `bot-llm-text`, etc.). The
`app-message` type never reaches the browser. Also, long reasoning text
causes `socket.send() raised exception`.

### V2: HTTP Polling (current, working)

Server-side global event queue + JS `setInterval` polling `GET /events`.

## Working Architecture

### Server: Global Event Queue (`src/pipeline.py`)

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

### HTTP Endpoint (`src/bot_js_client.py`)

```python
@app.get("/events")
async def get_events():
    from src.pipeline import pop_events
    return {"events": pop_events()}
```

### Client Polling (`app.js`)

```javascript
setInterval(async () => {
  try {
    const resp = await fetch(`${botBaseUrl}/events`);
    const data = await resp.json();
    if (data.events && data.events.length > 0) {
      for (const ev of data.events) {
        client.addEvent('server', ev);
      }
    }
  } catch (_) { /* ignore */ }
}, 1000);
```

### Event Sources (in `BotTextProcessor` and dedicated processors)

| Frame Type | Event Label | Chinese Marker |
|---|---|---|
| `TranscriptionFrame` | `server` | `【STT最终转录】` |
| `InterimTranscriptionFrame` | `server` | `【STT中间转录】` |
| `LLMContextFrame` | `server` | `【LLM输入】` |
| `LLMFullResponseStartFrame` | `server` | `【LLM开始】` |
| `LLMTextFrame` (skip_tts=False) | `server` | `【LLM输出】` |
| `LLMFullResponseEndFrame` | `server` | `【LLM结束】` |
| `TTSTextFrame` | `server` | `【TTS输入】` |

### Expected Events Panel Output

```
21:18:46 connected: Successfully connected to bot
21:18:48 server: 【LLM思考】用户
21:18:50 server: 【LLM输出】你好！我是你的智能语音助手...
21:18:51 server: 【LLM结束】思考=... 回答=...
21:19:06 test-audio: 已发送 82176B 测试语音
```

## Pitfalls

- **Events are consumed-on-read**: `pop_events()` clears the queue. Two
  browsers polling simultaneously will each see a subset.
- **Truncate long messages**: Thinking/reasoning text can be hundreds of
  chars. Cap at ~500 chars per event.
- **Don't send per-chunk thinking events**: DeepSeek streams reasoning one
  token at a time. Pushing each token as a separate event floods the queue.
  Only accumulate and show the full summary at `【LLM结束】`.
- **Polling interval**: 1s is fine for development. For production, consider
  SSE or WebSocket push instead.

## Pipeline Wiring

```python
processors = [
    transport.input(),
    RTVIProcessor(),
    stt,
    STTEventProcessor(),       # pushes STT events
    user_agg,
    LLMInputEventProcessor(),  # pushes LLM input events
    llm,
    BotTextProcessor(),        # pushes LLM + TTS events
    tts,
    transport.output(),
    assistant_agg,
]
```
