# DeepSeek/LiteLLM reasoning_content 修复记录 (2026-06-30)

## 现象

JS 客户端连接成功，LLM 被调用且有 completion tokens 输出，但 EdgeTTS 无声、BotTextProcessor 未收到任何 LLMTextFrame/LLMFullResponseStartFrame。

## 根因

LiteLLM/Headroom 代理将所有模型的流式响应内容放在 `delta.reasoning_content` 字段而非标准的 `delta.content`。

```
OpenAI 标准: {"delta":{"content":"你好"}}
LiteLLM代理: {"delta":{"reasoning_content":"你好"}}
```

Pipecat 的 `BaseOpenAILLMService._process_context` 在 `openai/services/openai/base_llm.py` 中只检查 `chunk.choices[0].delta.content`，该字段始终为 None。LLM 处理器在 pipeline 中的输出通过 `_push_llm_text` 调用 `push_frame(LLMTextFrame(text))` 后经过**异步队列**推送到 BotTextProcessor，但 LLMTextFrame 在队列中不被处理。

## 修复（两个 pip 包 patch）

### 1. OpenAI 模型层：ChoiceDelta 加 reasoning_content 字段

**文件**: `.venv/lib/python3.11/site-packages/openai/types/chat/chat_completion_chunk.py`

```python
class ChoiceDelta(BaseModel):
    content: Optional[str] = None
    function_call: Optional[ChoiceDeltaFunctionCall] = None
    reasoning_content: Optional[str] = None  # ← ADD
    refusal: Optional[str] = None
    role: Optional[...] = None
    tool_calls: Optional[...] = None
```

### 2. Pipecat LLM 服务：_process_context 处理 reasoning_content

**文件**: `.venv/lib/python3.11/site-packages/pipecat/services/openai/base_llm.py`

在 `elif chunk.choices[0].delta.content:` 之后添加：

```python
# LiteLLM/Headroom proxy sends content in reasoning_content
elif hasattr(chunk.choices[0].delta, "reasoning_content") and chunk.choices[0].delta.reasoning_content:
    await self._push_llm_text(chunk.choices[0].delta.reasoning_content)
```

## 验证方法

1. 在 BotTextProcessor 中加日志打印所有非音频帧（见 pipecat-voice-agent pitfall #19）
2. 连接后观察 `[BotText]` 日志
3. `reasoning_content` patch 生效后可见 `[DEEPSEEK-REASONING] text='xxx'`（需在 base_llm.py 中加 debug log）
4. 对应 BotText 日志可见 `[BotText] LLMFullResponseStartFrame` → `LLMTextFrame` → `LLMFullResponseEndFrame`

## 关联文件

- `~/workspace/pipecat/src/bot_js_client.py` - 服务端入口（端口 7860, SmallWebRTC + 测试音频注入）
- `~/workspace/pipecat/src/pipeline.py` - BotTextProcessor + build_pipeline
- `~/workspace/pipecat/src/services/edge_tts.py` - Edge TTSSettings + model/language=None 修复
- `~/workspace/pipecat/pipecat-examples/simple-chatbot/client/javascript/src/app.js` - JS 客户端
- `~/workspace/pipecat/pipecat-examples/simple-chatbot/client/javascript/index.html` - 浮动测试按钮

## 未解决问题

修复后 `_push_llm_text` 能捕获 reasoning_content 并调用 `push_frame(LLMTextFrame(text))`，但因 pipecat 框架的异步队列机制，BotTextProcessor 仍无法收到 LLMTextFrame（仅收到 `MetricsFrame`）。可能需将 BotTextProcessor 设为 `enable_direct_mode=True`。
