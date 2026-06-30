# PrebuiltUI BotOutput 消息机制（替代 BotTextProcessor）

2026-06-30 从官方源码发现。更新于同一 session 结束。

## 关键发现

**PrebuiltUI 的对话消息通过 `BotOutput` RTVI 事件渲染，不是 `bot-transcription`。**

客户端 `client-react` 源码（`index.js` 解包）:
```js
// V(L.BotOutput, ...) — PrebuiltUI 订阅的这个事件
V(L.BotOutput, callback)
```

服务端 `RTVIObserver` 在 `_handle_aggregated_llm_text()` 发送 `BotOutputMessage`。
触发条件：`AggregatedTextFrame` 从 `BaseOutputTransport` 经过时被 observer 捕获。

## 数据流向 (官方正确路径)

```
LLM → LLMTextFrame (逐 token)
  → TTS 生成 AggregatedTextFrame (tts_service.py:971)
  → AggregatedTextFrame 向下游推 → transport.output()
  → assistant_agg (聚合) → 向上游回穿 transport.output()
  → RTVIObserver 捕获（check: isinstance(src, BaseOutputTransport)）
  → 发送 RTVI BotOutputMessage → data channel → PrebuiltUI 渲染
```

## RTVIObserver 也发 bot-transcription（但将被废弃）

`observer.py:761-770` 中 `_handle_llm_text_frame()`:
```python
self._bot_transcription += frame.text
if match_endofsentence(self._bot_transcription):
    await self.send_rtvi_message(RTVI.BotTranscriptionMessage(...))
```

但有 TODO 注释: `# TODO (mrkb): Remove all this logic when we fully deprecate bot-transcription messages.`

**PrebuiltUI 没有注册 `onBotTranscript` 回调**（confirmed: 0 matches in minified bundle）。
PrebuiltUI 只注册了 `onBotConnected`、`onBotDisconnected`。（BotOutput 通过 React hook 内部订阅）

## RTVIObserver 必须连 RTVIProcessor — 否则静默丢弃

`observer.py:220` — `send_rtvi_message` 只有 `self._rtvi` 非 None 时才发消息：

```python
class RTVIObserver:
    def __init__(self, rtvi=None, ...):  # rtvi 默认为 None
        self._rtvi = rtvi

    async def send_rtvi_message(self, model, ...):
        if self._rtvi:  # ← None 时消息被丢弃
            await self._rtvi.push_transport_message(model, exclude_none)
```

`worker.py:385-405` — PipelineWorker 决定使用外部 observer 还是创建默认的：

```python
external_observer_found = any(isinstance(o, RTVIObserver) for o in observers)

if external_observer_found:
    # 使用用户传的 observer，但 rtvi=None → 静默丢弃！
else:
    # 自动创建连线 observer
    observers.append(self._rtvi.create_rtvi_observer(params=rtvi_observer_params))
```

### 正确写法 (code-helper 风格)

```python
# ❌ 错误: 手动传 RTVIObserver() → rtvi=None，所有消息丢弃
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(...),
    observers=[RTVIObserver()],  # self._rtvi = None
)

# ✅ 正确: 不传 observers=，让 Worker 自动创建连线 observer
worker = PipelineWorker(
    pipeline,
    params=PipelineParams(...),
    # 可选: rtvi_observer_params=RTVIObserverParams(...)
)
```

## 关键坑: RTVIObserver 的 src 守卫

`observer.py:483-495`:
```python
elif isinstance(frame, AggregatedTextFrame) and (
    self._params.bot_output_enabled or self._params.bot_tts_enabled
):
    if not isinstance(src, BaseOutputTransport):
        mark_as_seen = False  # ← 跳过！
    else:
        await self._handle_aggregated_llm_text(frame)  # ← 只有这里才发 BotOutput
```

问题：TTS 服务生成 `AggregatedTextFrame` 并向下游 push 时，`src=EdgeTTSService`，
不是 `BaseOutputTransport`，所以 observer 跳过了它。BotOutput 永远不会发送。

所以官方示例（`bot.py`）用 GeminiLiveLLMService（不走独立 TTS），LLM 直接输出 
AggregatedTextFrame 通过 transport.output()。我们的 pipeline 有独立 TTS，AggregatedTextFrame
来源是 TTS，被 observer 忽略。

### 当前的 pipeline 也不适用于分离的 TTS

我们的 pipeline:
```
input() → RTVIProcessor → stt → user_agg → llm →EdgeTTSService → output() → assistant_agg
```

AggregatedTextFrame 从 EdgeTTSService 发出（tts_service.py:971），src=TTS → 被 observer 跳过。
没有 BotOutput → PrebuiltUI 不显示 assistant 回复文本。

需要确保 AggregatedTextFrame 从 transport.output() 路径到达 observer。
官方路线没有现成的分离 TTS 范例。

### 已知官方轮子 (不推荐自定义 FrameProcessor)

- `LLMTextProcessor` (pipecat.processors.aggregators.llm_text_processor 中) — 
  官方类，把 LLMTextFrame 转成 AggregatedTextFrame。但 observer 的 src 守卫同样会跳过它。
- `RTVIObserverParams` — 没有参数可以放宽 src 守卫。
- `worker.rtvi.event_handler` — 可以注册自定义事件处理器但不修改 pipeline 帧流。

## 官方示例的正确 pipeline

`small-webrtc-prebuilt/test/bot.py:106-113`:
```python
pipeline = [
    transport.input(),
    user_aggregator,
    llm,            # GeminiLiveLLMService — TTS/STT 内置
    transport.output(),
    assistant_aggregator,
]
```

**没有 RTVIProcessor() 在列表里，没有自定义 FrameProcessor。**
**但这是为 GeminiLive 设计的，不能直接套用分离 TTS 的架构。**

## 不要自定义 FrameProcessor 推文字

旧方案: BotTextProcessor 拦截 TTSTextFrame → 推 OutputTransportMessageFrame → 卡 pipeline

正确方案: **什么都不做。** RTVIObserver 自动处理 BotOutput，PrebuiltUI 自动渲染。
如果一定需要自定义处理，用 `worker.rtvi.event_handler` 注册事件处理器：
```python
@worker.rtvi.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    context.add_message(...)
    await worker.queue_frames([LLMRunFrame()])
```

**绝对不要:**
- 不要继承 `FrameProcessor` 造自定义处理器
- 不要在 `process_frame` 里 push `OutputTransportMessageFrame`
- 不要在 pipeline 列表加 `RTVIProcessor()` （WorkerRunner 内置）
