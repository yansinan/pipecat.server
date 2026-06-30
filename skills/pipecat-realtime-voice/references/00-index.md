# Pipecat Real-Time Voice Agent — 索引

> 涵盖 SmallWebRTC + PrebuiltUI、FastAPIWebSocket、Daily 三种 transport。
> pitfall 按子系统分类在 `references/` 下。

## 快速导航

| 主题 | 位置 |
|---|---|
| Pipeline 结构（官方规范） | `references/pipeline-structure.md` |
| Transport 选型与对比 | `references/transport-overview.md` |
| SmallWebRTC + PrebuiltUI | `references/transport-webrtc.md` |
| FastAPIWebSocket + RawPCM | `references/transport-websocket.md` |
| HeadroomLLMService (reasoning_content) | `references/llm-reasoning-content.md` |
| STT 累积 + SegmentedSTTService | `references/stt-accumulation.md` |
| TTS 配置 + EdgeTTS 24kHz | `references/tts-edge.md` |
| RTVI / RTVIObserver / BotOutput | `references/rtvi-observer.md` |
| 测试音频注入 | `references/test-audio-injection.md` |
| CDP 浏览器测试 | `references/cdp-testing.md` |
| 调试原则（实证/自验/三击规则） | `references/debugging-principles.md` |
| Pitfalls 完整清单 | `references/pitfalls/` |
| 代码复用检查清单 | `references/framework-wheels.md` |

## Transport 选型

| Transport | 延迟 | 复杂度 | 浏览器端 | 适用场景 |
|---|---|---|---|---|
| FastAPIWebsocket + RawPCMSerializer | ~1s | 低 | 自建 HTML 页面 | 同机 / LAN 调试 |
| SmallWebRTC + PrebuiltUI | ~300ms | 中 | 官方 React SPA | 生产浏览器客户端 |
| Daily (pipecat-ai[daily]) | ~200ms | 中高 | Daily Prebuilt / 自建 | 跨 NAT / 多用户 |

## 官方 pipeline 结构（分离 STT + LLM + TTS）

仅存规范参考：`pipecat-examples/code-helper/server/bot.py:175-185`

```python
pipeline = Pipeline([
    transport.input(),
    stt,                    # 独立 STT
    user_aggregator,
    llm,
    llm_text_processor,     # 官方 LLMTextProcessor
    tts,                    # 独立 TTS
    transport.output(),
    assistant_aggregator,
])
```

**关键规则：**
- ✅ `LLMTextProcessor` 放 LLM 与 TTS 之间
- ✅ `WorkerRunner` 管理 RTVIProcessor + RTVIObserver（`worker.rtvi`）
- ❌ 不要手动放 `RTVIProcessor()` 进 pipeline 列表
- ❌ 不要传 `observers=[RTVIObserver()]`（让 WorkerRunner 自动创建）
- ❌ 不要继承 `FrameProcessor` 造自定义处理器
