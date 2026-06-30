# Official pipecat-examples Comparison (2026-06-30)

## Sources compared

| Source | Path | Purpose |
|--------|------|---------|
| `pipecat-examples/simple-chatbot/server/bot-openai.py` | GitHub | Daily transport reference (241 lines) |
| `small-webrtc-prebuilt/test/bot.py` | GitHub | SmallWebRTC-specific reference (166 lines) |
| `src/server_prebuilt.py` | our project | Our implementation (170 lines) |
| `src/pipeline.py` | our project | Our pipeline (115 lines) |

## Key findings by module

### TransportParams — no sample_rate in official examples

Official simple-chatbot `bot-openai.py` line 223-225:
```python
params=TransportParams(
    audio_in_enabled=True,
    audio_out_enabled=True,
)
```
No `audio_in_sample_rate` or `audio_out_sample_rate`. Official
small-webrtc bot.py line 59-65: same, no sample rates.

**But**: EdgeTTS outputs 24kHz, and default TransportParams negotiates
16kHz via SDP. 24→16 non-integer resampling is bad. Fix: either set
`audio_out_sample_rate=24000`, or add `AudioResampleProcessor`.

### VAD — mandatory in both official examples

simple-chatbot `bot-openai.py:33,142-146`:
```python
from pipecat.audio.vad.silero import SileroVADAnalyzer
...
user_params=LLMUserAggregatorParams(
    vad_analyzer=SileroVADAnalyzer(),
)
```

small-webrtc bot.py:27,28,36 — same imports, same usage.

Without VAD, `STTService.run_stt()` never triggers. This is why "no
text transcription" happens.

### EdgeTTS audio format — 24kHz MP3 confirmed

Source: `edge_tts/constants.py:41-45`:
```python
# The output format "audio-24khz-48kbitrate-mono-mp3" is a 48 kbps constant
MP3_BITRATE_BPS = 48_000
```

24 kHz + 48 kbps mono MP3. Decoded to PCM at 24 kHz before entering
the pipecat pipeline.

### TranscriptionFrame / InterimTranscriptionFrame — new constructor in 1.4.0

Pipecat 1.4.0 added `user_id` and `timestamp` as required fields
(source: `pipecat/frames/frames.py:445-467`):

```python
class TranscriptionFrame(TextFrame):
    user_id: str
    timestamp: str
    language: Language | None = None
    result: Any | None = None
    finalized: bool = False
```

Old code: `TranscriptionFrame(text, confidence)` →
**broken** in 1.4.0.
New code: `TranscriptionFrame(text=text, user_id="user", timestamp=str(time.time()))`

### Pipeline order — official vs ours

**Official simple-chatbot**:
```
transport.input() → STT → user_agg → LLM → TTS → TalkingAnimation → transport.output() → assistant_agg
```

**Ours**:
```
transport.input() → RTVIProcessor → STT → user_agg → LLM → TTS → transport.output() → assistant_agg
```

RTVIProcessor is needed for SmallWebRTC (Daily has it built-in).
TalkingAnimation is optional (visual metrics, not functional).

### on_client_ready — official opens conversation

simple-chatbot `bot-openai.py:178-182`:
```python
async def on_client_ready(rtvi):
    context.add_message({"role": "developer", "content": "Start by introducing yourself."})
    await worker.queue_frames([LLMRunFrame()])
```

We don't have this. Bot waits for user to speak first. Not a bug, but a
design difference.

### Route coverage — we cover more

| Route | official simple-chatbot | official small-webrtc | ours |
|-------|------------------------|----------------------|------|
| `POST /start` | ❌ Daily doesn't use | ✅ | ✅ |
| `POST /api/offer` | ❌ | ✅ | ✅ |
| `PATCH /api/offer` | ❌ | ✅ | ✅ |
| `POST /sessions/{id}/api/offer` | ❌ | ❌ | ✅ |
| `PATCH /sessions/{id}/api/offer` | ❌ | ❌ | ✅ |
| `GET / → /client/` | ❌ | ❌ | ✅ |
| `app.mount("/client", PipecatPrebuiltUI)` | ✅ | ✅ | ✅ |

The `/sessions/{id}` routes are needed because the client-side
SmallWebRTCTransport posts to that path, not bare `/api/offer`.

### background_tasks — official simple-chatbot doesn't need it

The official simple-chatbot uses Daily transport, where `run_bot` is not
inside an HTTP handler. For SmallWebRTC, `on_connection` runs inside
`handle_web_request()`, so `background_tasks.add_task()` is essential.

## Summary of diff priorities

| Priority | Item | Source |
|----------|------|--------|
| P0 | VAD (SileroVADAnalyzer) | both official examples |
| P0 | EdgeTTS 24kHz → match sample rate | edge-tts/constants.py |
| P0 | background_tasks pattern | discovered debugging |
| P1 | /sessions/{id}/api/offer route | discovered from client 404s |
| P1 | TranscriptionFrame timestamp+user_id | pipecat frames.py |
| P2 | on_client_ready opening line | simple-chatbot bot-openai.py:178 |
| P2 | build_pipeline keyword-only | signature check |
