# pipecat 1.4.0 — Frame Signature Changes & VAD Default

## Three changes that bite custom STT service authors

When wrapping your own STT service in 1.4.0, three things will fail silently or noisily:

### 1. `TranscriptionFrame` / `InterimTranscriptionFrame` require `user_id` + `timestamp`

Old signature (1.3.x and earlier):
```python
yield InterimTranscriptionFrame(text, confidence)
yield TranscriptionFrame(text, confidence)
```

New signature (1.4.0) — both are keyword-only dataclasses with required `user_id` and `timestamp`:

```python
yield InterimTranscriptionFrame(
    text=seg.text,
    user_id="user",
    timestamp=str(time.time()),
)
yield TranscriptionFrame(
    text=full_text.strip(),
    user_id="user",
    timestamp=str(time.time()),
)
```

Without these, you'll see:

```
TypeError: InterimTranscriptionFrame.__init__() missing 1 required positional argument: 'timestamp'
```

This is a **runtime** error inside your `run_stt()` async generator, not an import error. It can be hard to catch because it only fires on the first successful transcription.

### 2. VAD is required for any non-trivial STT

If you skip `vad_analyzer` in `LLMUserAggregatorParams`, the pipeline uses a **timeout-based** user-turn detection (default 2-3 seconds of silence). Symptoms:

- User speaks a short utterance → STT doesn't trigger
- Long pauses between sentences → STT triggers multiple times mid-sentence
- User text never appears in the chat panel

**Always set VAD explicitly**:

```python
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)

user_agg, assistant_agg = LLMContextAggregatorPair(
    context,
    user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(),    # <-- required, not optional
    ),
)
```

**Source** — official examples, both consistently set it:
- `pipecat-examples/simple-chatbot/server/bot-openai.py:145`
- `pipecat-ai/small-webrtc-prebuilt/test/bot.py`

This is the same for FastAPIWebsocketTransport — the websocket path also uses `LLMContextAggregatorPair` and needs the same `vad_analyzer` arg.

### 3. RTVIProcessor / RTVIObserver position in pipeline

`RTVIProcessor` must go **early** in the processors list (right after `transport.input()`) so it can intercept `InputTransportMessageFrame` (RTVI messages from the data channel) and push the appropriate response. `RTVIObserver` is a separate construct passed to `PipelineWorker(observers=[RTVIObserver()])` — they are NOT in the processors list.

```python
PipelineWorker(
    pipeline,
    params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    observers=[RTVIObserver()],   # <-- observer
)
```

```python
Pipeline(processors=[
    transport.input(),
    RTVIProcessor(),          # <-- processor (early)
    stt,
    user_agg,
    llm,
    tts,
    transport.output(),
    assistant_agg,
])
```

Without `RTVIProcessor`, the client never gets `bot-ready` and the `PipelineWorker` will print:
```
ERROR: RTVIProcessor found in pipeline but no RTVIObserver in observers.
```

It's a pairing — both must be present.

## Why these changes happened

The `TranscriptionFrame` signature change reflects a broader 1.4.0 shift toward multi-user / multi-channel pipelines. The VAD default change reflects pipecat's stance that VAD is the right primitive for turn detection, not silence timeout.

`frame.audio_out_sample_rate=16000` (the old default) was also removed — transports now either get the explicit value or read it from the SDP-negotiated codec rate. See the official example's `TransportParams(audio_in_enabled=True, audio_out_enabled=True)` (no sample_rate) for the canonical pattern.

## Verification checklist after upgrading to 1.4.0

- [ ] `InterimTranscriptionFrame` / `TranscriptionFrame` calls have `user_id=` and `timestamp=`
- [ ] `LLMContextAggregatorPair` has `user_params=LLMUserAggregatorParams(vad_analyzer=...)`
- [ ] `Pipeline(processors=[..., RTVIProcessor(), ...])` early in the list
- [ ] `PipelineWorker(..., observers=[RTVIObserver()])`
- [ ] `TransportParams(audio_in_enabled=True, audio_out_enabled=True)` (no sample_rate unless you've measured the resample path)
