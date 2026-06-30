# Round 3: Whisper + Bot-Text + Restart Script (2026-06-30)

After the first round got the client to "READY / READY" (RTVI handshake
green, ICE green, audio track received), the second round exposed three
more defects and one infrastructure annoyance.

## Bug 8: Whisper's `TranscriptionFrame` crashes with missing `timestamp` / `user_id`

**Symptom** (browser console):
```
Error: InterimTranscriptionFrame.__init__() missing 1 required
       positional argument: 'timestamp'
```

**Root cause**: In pipecat 1.4.0, `TranscriptionFrame` and
`InterimTranscriptionFrame` require keyword arguments:

```python
class TranscriptionFrame(TextFrame):
    user_id: str
    timestamp: str
    language: Language | None = None
    result: Any | None = None
    finalized: bool = False
```

The old (pre-1.4) positional call
`TranscriptionFrame(text, confidence)` no longer works.

**Fix** in `src/services/whisper_stt.py`:

```python
import time
yield InterimTranscriptionFrame(
    text=seg.text,
    user_id="user",
    timestamp=str(time.time()),
)
...
yield TranscriptionFrame(
    text=full_text.strip(),
    user_id="user",
    timestamp=str(time.time()),
)
```

## Bug 9: RTVI doesn't auto-send bot text back to the client

**Symptom**: Bot audio plays fine. LLM is called and you see
`OpenAILLMService#0: Generating chat from context [...]` in the log.
But the chat box in the browser never shows the assistant's reply.

**Root cause**: `RTVIProcessor` only handles RTVI protocol messages
(client-ready, bot-ready, send-text, function-call-result, etc.). It
does **not** automatically forward the assistant's `TTSTextFrame` to the
client's data channel. Daily-based transports get this for free
(Daily has its own transcription broadcast); `SmallWebRTC` does not.

**Architectural trap**: `TTSTextFrame` is **consumed by the TTSService**
— it calls `run_tts(text)` which produces audio frames, but the
original `TTSTextFrame` does NOT continue downstream. See pitfall 14
in SKILL.md for the full explanation.

**Two fixes**:

**Fix A (recommended) — Custom FrameProcessor BEFORE TTS**:

Place a `BotTextProcessor` between LLM and TTS so it catches
`TTSTextFrame` before TTS consumes it:

```python
class BotTextProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        # Must bypass _check_started — self.__started is name-mangled
        self._FrameProcessor__started = True

    async def process_frame(self, frame, direction):
        if isinstance(frame, TTSTextFrame):
            msg = {
                "type": "bot-transcription",
                "data": {"text": str(frame), "user_id": "assistant"},
            }
            # Push TTS text first (downstream), then the transport message
            await self.push_frame(frame, direction)
            await self.push_frame(
                OutputTransportMessageFrame(message=json.dumps(msg)),
                FrameDirection.UPSTREAM,
            )
        else:
            await self.push_frame(frame, direction)
```

Wire it between LLM and TTS:

```python
processors = [
    llm,
    BotTextProcessor(),          # catches TTSTextFrame BEFORE TTS
    tts,
    transport.output(),
    assistant_agg,
]
```

**Fix B — Yield from custom TTS `run_tts`**:

In a custom `TTSService` (like the project's `EdgeTTSService`), yield
`OutputTransportMessageFrame` right after `TTSStartedFrame`:

```python
yield TTSStartedFrame()
yield OutputTransportMessageFrame(
    message=json.dumps({
        "type": "bot-transcription",
        "data": {"text": text, "user_id": "assistant"},
    })
)
# then audio frames...
```

**⚠️ Known limitation**: Both approaches can have interaction with
pipeline interruption/flush. If the pipeline broadcasts an interruption
(due to client disconnect or idle timeout) between the two `push_frame`
calls, TTS may not fire. This is a pipecat pipeline lifecycle issue
not fully resolved as of 1.4.0 — see the full session transcript at
`references/bot-text-processor-lifecycle-2026-06-30.md`.

## Bug 10: Don't pin `audio_*_sample_rate` in TransportParams

**Symptom**: TTS audio plays but sounds metallic / choppy / stuttering.

**Root cause**: I had `audio_in_sample_rate=16000, audio_out_sample_rate=16000`
copied from the old `bot.py` (RawPCM path). But the EdgeTTS path
delivers 24kHz MP3 → decoded to 16kHz PCM by ffmpeg → goes back to
24kHz via the small-webrtc transport, which then has to resample to
Opus/48kHz. The non-integer resample is lossy and the artefact
sounds like clipping.

**Fix**: Don't set sample_rate. Let aiortc negotiate. Verified by
comparing: with `audio_*_sample_rate=16000` the audio was clearly
broken; with the parameters omitted, audio is clean (per browser
listening test).

```python
TransportParams(
    audio_in_enabled=True,
    audio_out_enabled=True,
    # NO sample_rate fields
    video_in_enabled=False,
    video_out_enabled=False,
)
```

## Bug 11: `VAD is required` for `STTService` to fire

**Symptom**: With the VAD removed, `STTService.run_stt()` either never
fires or only fires after a long silence. The user speaks, nothing
happens for several seconds, then maybe a transcription appears.

**Root cause**: Without a VAD, the user-aggregator has no signal for
"the user finished speaking". It waits for either:
- the configured idle timeout (long), or
- a `UserStoppedSpeakingFrame` (which only exists with a VAD).

With VAD: as soon as the user stops talking, `SileroVADAnalyzer`
emits a stop event, the aggregator forwards the audio buffer to STT,
and the pipeline moves on to LLM.

**Fix** (verified against `bot-openai.py:145` in the official
`pipecat-examples/simple-chatbot`):

```python
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)

user_agg, assistant_agg = LLMContextAggregatorPair(
    context,
    user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),
    ),
)
```

## Bug 12: "字幕by索兰娅" was Whisper doing its job, not context pollution

When the user spoke, Whisper picked up the actual speech and
transcribed it. The first turn logged
`[{'role': 'user', 'content': '字幕by索兰娅'}, ...]` — the
`'字幕by索兰娅'` was the genuine STT output, not random context from
elsewhere. If the transcription is wrong, it's a Whisper
mis-recognition, not a bug in our pipeline. VAD is working.

## Bug 13: Background server outputs to file → impossible to read

`hermes` background processes dump stdout/stderr to a pipe that
`process(action="log")` returns in a compressed, fragmented form.
Reading the server log via `read_file` truncated to ~1500 chars
per call. The `cat /proc/$PID/fd/1` trick hung forever.

**Workaround that actually works**:

- Redirect the server's stdout/stderr to a file when launching, NOT
  to the background pipe:
  ```bash
  uv run --project . python -m src.server_prebuilt \
      > /home/dr/workspace/pipecat/.cache-uv/server.log 2>&1
  ```
- Don't try to read the file with `read_file` — it always gets
  compressed. Instead, run `tail` inside an `execute_code` block
  and print the result with `print(l[:200])` per line. This
  bypasses the compression for short snippets.
- If `read_file` is the only option, paginate: `offset=N, limit=20`.

## Bug 14: Forward `Ctrl+C` cleanly through `bash restart_server.sh`

The user wants a `restart_server.sh` that:
1. Kills the previous instance.
2. Starts the server in the **current terminal** (not backgrounded).
3. Lets them hit Ctrl+C and have it actually quit.

`exec uv run …` swallows the SIGINT. Solution: background with `&`,
`wait`, and a `trap` that forwards the signal:

```bash
#!/bin/bash
# Restart Pipecat PrebuiltUI server (8766) — Ctrl+C clean exit.
cd /home/dr/workspace/pipecat

# Kill only the server_prebuilt python process, not other python services.
killall -9 -r "python.*server_prebuilt" 2>/dev/null
sleep 2
# Belt and suspenders — by port.
for pid in $(ss -tlnp 2>/dev/null | grep 8766 | grep -oP 'pid=\K\d+'); do
    kill -9 "$pid" 2>/dev/null
done
sleep 1

echo "=== Code timestamps ==="
stat --format="%y  %n" src/server_prebuilt.py src/pipeline.py

export PATH="/home/dr/.hermes/bin:$PATH"
echo "=== Server: http://localhost:8766/ (Ctrl+C to stop) ==="

uv run --project . python -m src.server_prebuilt &
SERVER_PID=$!
trap "echo '[stop] killing PID '$SERVER_PID; \
      kill -INT $SERVER_PID 2>/dev/null; \
      wait $SERVER_PID 2>/dev/null; \
      exit 0" INT TERM
wait $SERVER_PID
```

Key choices:
- `killall -9 -r "python.*server_prebuilt"` is **scoped**: it only
  matches processes whose cmdline contains `server_prebuilt`. Don't
  use bare `killall python` — that will also kill unrelated pipecat
  bots (e.g. `bot.py` on port 8765).
- `kill -INT` (not `-9`) on the child so uvicorn runs its shutdown
  handlers. Then `wait` so the parent process exits cleanly.

## Source pointers

- `InterimTranscriptionFrame` and `TranscriptionFrame` shapes:
  `pipecat/frames/frames.py:445-475` in the installed wheel.
- Official `vad_analyzer=SileroVADAnalyzer()` usage:
  `pipecat-examples/simple-chatbot/server/bot-openai.py:33, 142-147`.
- `pipecat.workers.runner.WorkerRunner.run(auto_end=False)`:
  required because `PipelineWorker.run()` is missing the `params`
  arg in 1.4.0.
- EdgeTTS output format: 24kHz MP3, decoded by ffmpeg in our
  `EdgeTTSService` to 16kHz PCM.
