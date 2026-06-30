# DeepSeek + LiteLLM Δ.reasoning_content Fix

## The Bug

DeepSeek models (v4-flash, R1, etc.) **and** MiniMax models through LiteLLM/Headroom proxy
**always** return generated text in `delta.reasoning_content` instead of `delta.content`:

```json
{"choices":[{"index":0,"delta":{"reasoning_content":"你好","role":"assistant"}}]}
```

Pipecat's `BaseOpenAILLMService._process_context()` only reads `delta.content`, so **all**
LLM output is silently dropped. The API returns completion tokens (shown in metrics) but no
text reaches downstream processors (BotText, TTS).

## Diagnosis

```bash
curl -s -X POST "http://your-litellm-url/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}],"stream":true,"max_tokens":30}' \
  | grep 'delta'
```

If **every** data line shows `reasoning_content` and **none** show `content`, you have this bug.

## Full Fix (3 patches)

All patches are applied to the Python **venv** — one-time per environment. They survive
venv rebuilds only if scripted; otherwise re-apply after `uv sync` or pip reinstall.

### Patch 1 — pydantic model: `openai/types/chat/chat_completion_chunk.py`

File location: `$VENV/lib/python3.11/site-packages/openai/types/chat/chat_completion_chunk.py`

Add `reasoning_content: Optional[str] = None` to the `ChoiceDelta` class:

```python
class ChoiceDelta(BaseModel):
    content: Optional[str] = None
    function_call: Optional[ChoiceDeltaFunctionCall] = None
    reasoning_content: Optional[str] = None   # ← add this
    refusal: Optional[str] = None
    role: Optional[Literal[...]] = None
    tool_calls: Optional[List[ChoiceDeltaToolCall]] = None
```

Without this, pydantic silently drops `reasoning_content` from the parsed JSON.
`hasattr(delta, "reasoning_content")` returns `False` even though the JSON had the key.

### Patch 2 — processing logic: `pipecat/services/openai/base_llm.py`

File location: `$VENV/lib/python3.11/site-packages/pipecat/services/openai/base_llm.py`

In `_process_context()`, add an `elif` branch after the `delta.content` check:

```python
                elif chunk.choices[0].delta.content:
                    await self._push_llm_text(chunk.choices[0].delta.content)
                # LiteLLM/Headroom: DeepSeek content lives in reasoning_content
                elif hasattr(chunk.choices[0].delta, "reasoning_content") and chunk.choices[0].delta.reasoning_content:
                    await self._push_llm_text(chunk.choices[0].delta.reasoning_content)
```

This is the standard OpenAI SDK's delta object. The `reasoning_content` field follows
OpenAI's own spec for reasoning models but DeepSeek (and MiniMax through LiteLLM) uses
it for ALL text output.

### Patch 3 — verify syntax

```python
import py_compile
py_compile.compile("$VENV/.../base_llm.py", doraise=True)
```

## Verification

After restarting the server:

1. Connect a client
2. The server log should show `[BotText]` entries like:
   ```
   [BotText] LLMFullResponseStartFrame
   [BotText] LLMTextFrame text=你好！
   [BotText] LLMFullResponseEndFrame
   ```
3. If the server uses DebugFrameProcessor (see main skill), you'll also see:
   ```
   [Frame:after-llm] LLMTextFrame text=...
   [Frame:after-tts] TTSAudioRawFrame audio=640B
   ```

## Known Caveat

This patch routes the model's **thinking tokens** (internal monologue) through to TTS.
DeepSeek doesn't separate reasoning_content from the final answer — ALL output comes
through the same field. The bot may speak its reasoning process aloud.

**Production fix**: Configure the LiteLLM proxy (serverhome) to merge `reasoning_content`
into `content` at the proxy level. Then no pipecat patches are needed.

## Models Affected (verified)

| Model | Uses reasoning_content? |
|-------|------------------------|
| `deepseek-v4-flash` | ✅ Yes — ALL output |
| `minimax` / `minimax-m3` | ✅ Yes — ALL output |
| `deepseek-chat` | ❌ Not available on this proxy |
| `gpt-4o-mini` | ❓ Not tested |
