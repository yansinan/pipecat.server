# STT Audio Accumulation + skip_tts Debugging (2026-06-30)

## Problem: STT never transcribed test audio

Symptoms:
- WebRTC connection succeeds (ICE completed)
- Audio flows as `InputAudioRawFrame` (640B chunks from browser)
- `[WHISPER-STT] run_stt called with 640B audio` × 128+ times
- No `TranscriptionFrame` or `InterimTranscriptionFrame` ever emitted
- VAD detects speech start/stop (`User started / stopped speaking`)
- But no text appears anywhere downstream

## Root cause: wrong base class

`WhisperSTTService` extended `STTService`. The base class's
`process_audio_frame` calls `run_stt(frame.audio)` for **every single
audio frame** — each 640B = 20ms of audio. Whisper cannot transcribe
20ms clips. No text is ever produced.

## Fix: inheriting from `SegmentedSTTService`

`SegmentedSTTService` extends `STTService` and overrides
`process_audio_frame` to **accumulate** audio into `_audio_buffer`.
When `VADUserStoppedSpeakingFrame` arrives (pushed UPSTREAM from the
`LLMUserAggregator`'s VAD), `_handle_user_stopped_speaking` writes the
buffer to a WAV and calls `run_stt(content.read())` with the full
accumulated utterance.

### Source code evidence

`stt_service.py` L766-773 (SegmentedSTTService.process_frame):
```python
if isinstance(frame, VADUserStartedSpeakingFrame):
    await self._handle_user_started_speaking(frame)
elif isinstance(frame, VADUserStoppedSpeakingFrame):
    await self._handle_user_stopped_speaking(frame)
```

`stt_service.py` L778-793 (SegmentedSTTService._handle_user_stopped_speaking):
```python
async def _handle_user_stopped_speaking(self, frame):
    self._user_speaking = False
    content = io.BytesIO()
    wav = wave.open(content, "wb")
    wav.setsampwidth(2); wav.setnchannels(1); wav.setframerate(self.sample_rate)
    wav.writeframes(self._audio_buffer)  # ← full accumulated audio
    self._audio_buffer.clear()
    await self.process_generator(self.run_stt(content.read()))  # ← full audio
```

`llm_response_universal.py` L1155-1165 (VAD signal pushed UPSTREAM):
```python
async def _on_vad_speech_stopped(self, controller):
    await self._queued_broadcast_frame(
        VADUserStoppedSpeakingFrame,
        stop_secs=controller._vad_analyzer.params.stop_secs,
    )

def _queued_broadcast_frame(self, frame_cls, **kwargs):
    await self.queue_frame(frame_cls(**kwargs))
    await self.push_frame(frame_cls(**kwargs), FrameDirection.UPSTREAM)
```

### Verification

Before fix: `run_stt called with 640B audio` (every 20ms)
After fix:  `run_stt called with 26252B audio from _handle_user_stopped_speaking` (full utterance)

---

## Problem: LLM thinking text spoken by TTS

The `reasoning_content` patch (pitfall 18) pushed `LLMTextFrame` for
thinking content, which went to EdgeTTS and was audibly spoken as
"用户说你好" etc.

### Fix: `skip_tts` with `field(init=False)` caveat

`TextFrame.skip_tts` is declared as `field(init=False)` in
`pipecat/frames/frames.py`:

```python
class TextFrame(DataFrame):
    text: str
    skip_tts: bool | None = field(init=False)  # NOT a constructor param!
```

So `LLMTextFrame(text, skip_tts=True)` raises:
```
LLMTextFrame.__init__() got an unexpected keyword argument 'skip_tts'
```

**Correct approach: set after construction:**
```python
frame = LLMTextFrame(reasoning_chunk)
frame.skip_tts = True
await self.push_frame(frame)
```

The `TTS Service` checks `frame.skip_tts` at `tts_service.py` line:
```python
if isinstance(frame, TextFrame) and not frame.skip_tts:
    # process for TTS
```

---

## Problem: LLMTextFrame never reaches BotText

Even with the `reasoning_content` patch, BotText saw only `MetricsFrame`
— no `LLMFullResponseStartFrame`, `LLMTextFrame`, or
`LLMFullResponseEndFrame`. The frames were queued but the pipeline
worker stopped (WebRTC connection closed) before the queue was drained.

See pitfall 19 in SKILL.md for details.
