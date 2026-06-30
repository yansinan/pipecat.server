# BotTextProcessor lifecycle: `_check_started` and TTSTextFrame routing (2026-06-30)

## The two bugs

### Bug A: `_check_started` checks `self.__started` (name-mangled), NOT `self._started`

`frame_processor.py` (compiled .so in pipecat 1.4.0):

```python
def _check_started(self, frame):
    if not self.__started:
        logger.error(f"{self} Trying to process {frame} but StartFrame not received yet")
    return self.__started
```

The double-underscore `__started` triggers Python name mangling:
`self.__started` → `self._FrameProcessor__started`.

Setting `self._started = True` in a subclass creates a NEW attribute
`self._started` — it does NOT affect `self.__started` (which remains
False from `FrameProcessor.__init__`).

**Fix**: 
```python
class MyProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._FrameProcessor__started = True  # bypass _check_started
```

**Verification**: After this fix, the error log
"BotTextProcessor#0 Trying to process InputAudioRawFrame but StartFrame not received yet"
completely disappeared from the server log.

### Bug B: TTSTextFrame is consumed by TTSService internally

`TTSService.process_frame` handles `TTSTextFrame` by calling
`run_tts(text)`. It does NOT call `push_frame(TTSTextFrame)` downstream.
The frame is consumed — only `TTSStartedFrame`, `TTSAudioRawFrame`, and
`TTSStoppedFrame` are produced.

**Wrong placement** (wastes time debugging):
```python
processors = [
    llm,
    tts,
    BotTextProcessor(),         # ← NEVER sees TTSTextFrame
    transport.output(),
]
```

**Correct placement**:
```python
processors = [
    llm,
    BotTextProcessor(),         # ← catches TTSTextFrame BEFORE TTS
    tts,
    transport.output(),
]
```

## Push order matters

When both `push_frame` calls are inside `process_frame`, the order
affects pipeline lifecycle:

```python
async def process_frame(self, frame, direction):
    if isinstance(frame, TTSTextFrame):
        # Order A: TTS first, then transport message
        await self.push_frame(frame, direction)                # → TTS
        await self.push_frame(output_msg, UPSTREAM)            # → RTVI

        # Order B: transport message first, then TTS
        # await self.push_frame(output_msg, UPSTREAM)
        # await self.push_frame(frame, direction)
    else:
        await self.push_frame(frame, direction)
```

With Order A (TTS first), the TTS pipeline starts. Then the UPSTREAM
push goes through RTVIProcessor which might broadcast an interruption
if it processes the bot-transcription message. This interruption can
prevent TTS from completing.

With Order B, the UPSTREAM push interrupts the pipeline, and the
downstream TTSTextFrame push may never reach TTS.

Both orders have been tested and both can fail under certain conditions
(pipeline flush timeout, RTVI interruption broadcast, idle timeout).
The fundamental issue is that two `push_frame` calls inside a single
`process_frame` are not atomic — the pipeline state can change between
them.

## Alternative: Yield from TTS `run_tts`

The most reliable approach tested: yield `OutputTransportMessageFrame`
from the custom `TTSService.run_tts()` generator:

```python
yield TTSStartedFrame()
yield OutputTransportMessageFrame(
    message=json.dumps({
        "type": "bot-transcription",
        "data": {"text": text, "user_id": "assistant"},
    })
)
# then TTSAudioRawFrame, TTSStoppedFrame
```

This works because `_stream_audio_frames_from_iterator` in the base
`TTSService` calls `push_frame()` for every yielded frame, and the
pipeline is in a "steady" state (no concurrent push_frame).

## Evidence collected

LLM input/output was verified across 3 separate sessions:

1. Input: "回复你好" → Output: 160 completion tokens
2. Input: "你好，请回复测试" → Output: 39 completion tokens  
3. Input: "simple test" → Output: 60 completion tokens (with cache)
4. Input: "tell me a short story" → Output: 50 completion tokens

All processed by deepseek-v4-flash via LiteLLM/Headroom proxy.

Log evidence format:
```
18:15:16.376 | DEBUG | OpenAILLMService#1: Generating chat from context 
           [{'role': 'user', 'content': 'simple test'}]
18:15:17.706 | TTFB: 1.330s
18:15:18.930 | prompt tokens: 381, completion tokens: 60, 
               cache read input tokens: 256, reasoning tokens: 23
18:15:19.089 | processing time: 2.713s
```

## Still unresolved (as of 2026-06-30)

- TTS audio not firing when BotTextProcessor is in the pipeline
- Text transcription not reaching PrebuiltUI conversation panel
  (OutputTransportMessageFrame may be dropped or malformed)
- Interaction between interruption broadcast and downstream push_frame
  causes pipeline flush timeout

## Files modified in this session

| File | Change |
|------|--------|
| `src/pipeline.py` | BotTextProcessor added/removed 5×; final revert to bare pipeline |
| `src/services/edge_tts.py` | Added OutputTransportMessageFrame yield in run_tts |
| `.env` | LLM_MODEL changed from minimax to deepseek-v4-flash |
| `src/server_prebuilt.py` | STUN server restored (was empty causing INITIALIZED hang) |
