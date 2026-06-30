# STT Audio Accumulation — WhisperSTTService must extend SegmentedSTTService

## Symptom

`[WHISPER-STT] run_stt called with 640B audio` logged hundreds of times, but NO
`TranscriptionFrame` or `InterimTranscriptionFrame` ever appears. VAD detects speech
start/stop correctly, but Whisper outputs nothing.

## Root cause

`WhisperSTTService` extends `STTService` which processes every audio frame individually.

**Source code chain (pipecat 1.4.0):**

### 1. Audio arrives in 640B chunks

WebRTC transports deliver audio in 20ms frames (640 bytes @ 16kHz mono).

### 2. STTService.process_audio_frame calls run_stt on EVERY frame

```
stt_service.py L394:  await self.process_generator(self.run_stt(frame.audio))
```

`frame.audio` is 640B — 20ms of audio. Whisper with `vad_filter=True` filters these
out as non-speech because 20ms is far too short for meaningful speech detection.

### 3. SegmentedSTTService accumulates instead

```
stt_service.py L815:  self._audio_buffer += frame.audio
```

Extends `STTService`. Overrides `process_audio_frame` to accumulate audio into
`self._audio_buffer` (bytearray). Does NOT call `run_stt` per frame.

### 4. VAD triggers transcription via UPSTREAM frame

When `LLMUserAggregator`'s VAD detects speech stop, it broadcasts
`VADUserStoppedSpeakingFrame` UPSTREAM:

```
llm_response_universal.py L1161-1165:
    async def _on_vad_speech_stopped(self, controller):
        await self._queued_broadcast_frame(
            VADUserStoppedSpeakingFrame,
            stop_secs=controller._vad_analyzer.params.stop_secs,
        )

llm_response_universal.py (near L1152):
    def _queued_broadcast_frame(self, frame_cls, **kwargs):
        await self.queue_frame(frame_cls(**kwargs))
        await self.push_frame(frame_cls(**kwargs), FrameDirection.UPSTREAM)  # ← KEY
```

The UPSTREAM push goes to `self._prev` — the STT service (which is before user_agg
in the pipeline).

### 5. SegmentedSTTService handles the VAD frame

```
stt_service.py L772-773:
    elif isinstance(frame, VADUserStoppedSpeakingFrame):
        await self._handle_user_stopped_speaking(frame)

stt_service.py L778-793:
    async def _handle_user_stopped_speaking(self, frame):
        self._user_speaking = False
        content = io.BytesIO()
        wav = wave.open(content, "wb")
        wav.setsampwidth(2)
        wav.setnchannels(1)
        wav.setframerate(self.sample_rate)
        wav.writeframes(self._audio_buffer)    # ← accumulated audio!
        wav.close()
        content.seek(0)
        self._audio_buffer.clear()
        await self.process_generator(self.run_stt(content.read()))  # ← full audio!
```

## Fix

```python
# ❌ Wrong — no accumulation:
from pipecat.services.stt_service import STTService
class WhisperSTTService(STTService): ...

# ✅ Correct — accumulates + VAD-triggered:
from pipecat.services.stt_service import SegmentedSTTService
class WhisperSTTService(SegmentedSTTService): ...
```

## Verification

**Direct STT test** (bypasses pipeline, use as first diagnostic step):
```bash
uv run python3 -c "
import asyncio
from src.services.whisper_stt import WhisperSTTService
stt = WhisperSTTService(model_size='small')
with open('/tmp/test_speech.pcm','rb') as f: audio = f.read()
async def test():
    async for frame in stt.run_stt(audio):
        if frame and hasattr(frame,'text'): print(f'STT: {frame.text}')
asyncio.run(test())
"
```

**Pipeline test** (after fix): server log shows:
```
_handle_user_stopped_speaking → run_stt called with 82000+B audio
```
instead of:
```
run_stt called with 640B audio  (repeated 100+ times)
```

## Pitfall — settings NOT_GIVEN warning

When using `SegmentedSTTService`, you may also need to suppress the
`STTSettings: model, language fields are NOT_GIVEN` warning:

```python
class WhisperSTTSettings(STTSettings):
    model: str | None = None
    language: str | None = None
```
