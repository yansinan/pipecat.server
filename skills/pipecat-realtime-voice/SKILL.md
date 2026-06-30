---
name: pipecat-realtime-voice
description: "Pipecat 实时语音 Agent — SmallWebRTC / WebSocket / Daily transport. 涵盖 pipeline 结构、pitfall 排查、CDP 测试、部署。加载后先读 `references/00-index.md`"
version: 1.4.0
platforms: [linux]
metadata:
  hermes:
    tags: [pipecat, voice-agent, webrtc, websocket, fastapi, real-time, audio, prebuilt-ui, aiortc, headroom, reasoning-content]
    related_skills: [pipecat-voice-agent-project, service-unreachable-diagnosis, systematic-debugging]
---

# Pipecat Real-Time Voice Agent

## When to use

- 添加浏览器语音界面（任意 transport）
- 配置 SmallWebRTC + PrebuiltUI
- 配置 FastAPIWebSocket + RawPCM
- 调试 RTVI / WorkerRunner / 浏览器无声 / AudioContext

## 先读这个

`references/00-index.md` → 按需跳转对应文件。

## 核心原则

1. **先搜框架源码再写自定义实现** — `grep -r "def process_frame" .venv/.../pipecat/`
2. **改完自己验证** — 三击规则：第 3 次要求用户测试 = 失去信任
3. **不以正弦波降级** — 缺少依赖直接报错，不静默 fallback
4. **不写空壳函数** — 删功能就一并删调用方和端点

## Pitfalls 速查

| 现象 | 根因 | 快速修复 |
|---|---|---|
| PrebuiltUI 卡在 "authenticating" | 无 `/start` 端点或返回缺字段 | 返回 `{"sessionId":..., "iceConfig":{...}}` |
| 浏览器显示 "connecting" 但 `/api/offer 404` | 未注册 `/sessions/{id}/` 路径 | 只注册带 session_id 的路径 |
| LLM 有 tokens 但无 TTS 输出 | `reasoning_content` 未处理 | 加载 `references/llm-reasoning-content.md` |
| LLM 有输出但 PrebuiltUI 对话面板空白 | `observers=[RTVIObserver()]` 静默丢消息 | 不传 `observers=`，让 WorkerRunner 自动创建 |
| 浏览器连接后 Bot 30 秒无响应 | Whisper 模型首次加载 ~27s | 模块级预加载 WhisperSTTService |
| `/api/offer` 超时 | on_connection 里 `await` 了 pipeline | 用 `background_tasks.add_task()` |
| 音频卡顿/杂音 | EdgeTTS 24kHz 与 transport 16kHz 不匹配 | `audio_out_sample_rate=24000` |
| 编译报 `SmallWebRTCTransportParams` | 类名不存在 | 用 `pipecat.transports.base_transport.TransportParams` |
| 自定义 FrameProcessor 不工作 | `__started` 被 name-mangling | 设 `self._FrameProcessor__started = True` |
| Vite dev 从别的机器访问不到 | 默认绑定 localhost | `vite.config.js` 加 `host: '0.0.0.0'` |
| 跨机器访问时 localhost 重定向错 | 写死了 localhost | 用 `request.url.hostname` 动态构造 |
